"""Target-window policy for delayed real-time transcription results."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from .foreground import (
    IntegrityComparison,
    IntegrityLevel,
    WindowMetadata,
    capture_foreground,
    capture_window,
    compare_integrity,
    get_current_integrity,
    is_window,
    request_foreground,
)


class TargetPolicy(StrEnum):
    LOCKED = "locked"
    FOLLOW_FOCUS = "follow_focus"


class TargetStatus(StrEnum):
    READY = "ready"
    NO_TARGET = "no_target"
    TARGET_INACTIVE = "target_inactive"
    TARGET_CLOSED = "target_closed"
    TARGET_REUSED = "target_reused"
    OWN_WINDOW = "own_window"
    HIGHER_INTEGRITY = "higher_integrity"
    INTEGRITY_UNKNOWN = "integrity_unknown"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class TargetDecision:
    status: TargetStatus
    target: WindowMetadata | None
    foreground: WindowMetadata | None
    reason: str

    @property
    def can_inject(self) -> bool:
        return self.status is TargetStatus.READY and self.target is not None

    @property
    def should_queue(self) -> bool:
        return self.status in {
            TargetStatus.NO_TARGET,
            TargetStatus.TARGET_INACTIVE,
            TargetStatus.TARGET_CLOSED,
            TargetStatus.TARGET_REUSED,
            TargetStatus.SUSPENDED,
        }

    @property
    def requires_safe_copy(self) -> bool:
        return self.status in {
            TargetStatus.OWN_WINDOW,
            TargetStatus.HIGHER_INTEGRITY,
            TargetStatus.INTEGRITY_UNKNOWN,
        }


ForegroundProvider = Callable[[], WindowMetadata | None]
WindowProvider = Callable[[int], WindowMetadata | None]
WindowExists = Callable[[int], bool]
FocusRequester = Callable[[int], bool]


class TargetWindowManager:
    """Resolve the only window to which a committed fragment may be sent.

    ``LOCKED`` is the safe default. It pauses while another window is active.
    ``FOLLOW_FOCUS`` must be selected explicitly and re-evaluates the foreground
    window for every insertion.
    """

    def __init__(
        self,
        *,
        policy: TargetPolicy | str = TargetPolicy.LOCKED,
        own_process_id: int | None = None,
        own_hwnds: Iterable[int] = (),
        current_integrity: IntegrityLevel | None = None,
        foreground_provider: ForegroundProvider = capture_foreground,
        window_provider: WindowProvider = capture_window,
        window_exists: WindowExists = is_window,
        focus_requester: FocusRequester = request_foreground,
        protected_process_names: Iterable[str] = ("voxtype.exe",),
    ) -> None:
        self._policy = TargetPolicy(policy)
        self._own_process_id = os.getpid() if own_process_id is None else own_process_id
        self._own_hwnds = {int(hwnd) for hwnd in own_hwnds if hwnd}
        if current_integrity is None:
            current_integrity = (
                get_current_integrity() if os.name == "nt" else IntegrityLevel.UNKNOWN
            )
        self._current_integrity = current_integrity
        self._foreground_provider = foreground_provider
        self._window_provider = window_provider
        self._window_exists = window_exists
        self._focus_requester = focus_requester
        self._protected_process_names = {
            name.casefold() for name in protected_process_names if name
        }
        self._locked_target: WindowMetadata | None = None
        self._suspended = False
        self._lock = threading.RLock()

    @property
    def policy(self) -> TargetPolicy:
        with self._lock:
            return self._policy

    @property
    def locked_target(self) -> WindowMetadata | None:
        with self._lock:
            return self._locked_target

    def set_policy(self, policy: TargetPolicy | str) -> None:
        with self._lock:
            self._policy = TargetPolicy(policy)

    def register_own_window(self, hwnd: int) -> None:
        if hwnd:
            with self._lock:
                self._own_hwnds.add(int(hwnd))

    def unregister_own_window(self, hwnd: int) -> None:
        with self._lock:
            self._own_hwnds.discard(int(hwnd))

    def start_session(self) -> TargetDecision:
        """Capture the foreground identity and security metadata once."""

        foreground = self._foreground_provider()
        with self._lock:
            blocked = self._blocked_status(foreground) if foreground is not None else None
            # A click on VoxType necessarily makes its own main window
            # foreground.  Retain the most recently observed external target so
            # the Start button behaves like the global hotkey.
            if blocked is not TargetStatus.OWN_WINDOW:
                self._locked_target = foreground
            self._suspended = False
        return self.resolve(foreground=foreground)

    def remember_foreground_target(self) -> WindowMetadata | None:
        """Remember a safe external foreground while no session is active."""

        foreground = self._foreground_provider()
        if foreground is None or self._blocked_status(foreground) is not None:
            return None
        with self._lock:
            self._locked_target = foreground
        return foreground

    def set_locked_target(self, target: WindowMetadata | None) -> None:
        """Restore a journalled target or provide one in deterministic tests."""

        with self._lock:
            self._locked_target = target

    def end_session(self) -> None:
        with self._lock:
            self._locked_target = None
            self._suspended = False

    def on_suspend(self) -> None:
        with self._lock:
            self._suspended = True

    def on_resume(self) -> None:
        with self._lock:
            self._suspended = False

    def _blocked_status(self, target: WindowMetadata) -> TargetStatus | None:
        if (
            target.process_id == self._own_process_id
            or target.hwnd in self._own_hwnds
            or target.process_name.casefold() in self._protected_process_names
        ):
            return TargetStatus.OWN_WINDOW
        comparison = compare_integrity(target.integrity_level, self._current_integrity)
        if comparison is IntegrityComparison.TARGET_HIGHER:
            return TargetStatus.HIGHER_INTEGRITY
        if comparison is IntegrityComparison.UNKNOWN:
            return TargetStatus.INTEGRITY_UNKNOWN
        return None

    @staticmethod
    def _reason(status: TargetStatus) -> str:
        reasons = {
            TargetStatus.READY: "Target window is active and safe",
            TargetStatus.NO_TARGET: "No target window has been captured",
            TargetStatus.TARGET_INACTIVE: "Locked target is not the foreground window",
            TargetStatus.TARGET_CLOSED: "Target window no longer exists",
            TargetStatus.TARGET_REUSED: "Target HWND now belongs to another process",
            TargetStatus.OWN_WINDOW: "VoxType never injects into its own windows",
            TargetStatus.HIGHER_INTEGRITY: "UIPI blocks input into a higher-integrity process",
            TargetStatus.INTEGRITY_UNKNOWN: "Target integrity could not be verified safely",
            TargetStatus.SUSPENDED: "Windows session is suspended or resuming",
        }
        return reasons[status]

    def resolve(self, *, foreground: WindowMetadata | None = None) -> TargetDecision:
        """Return a fresh decision immediately before an injection attempt."""

        with self._lock:
            policy = self._policy
            locked = self._locked_target
            suspended = self._suspended
        current = self._foreground_provider() if foreground is None else foreground
        if suspended:
            return TargetDecision(
                TargetStatus.SUSPENDED,
                locked if policy is TargetPolicy.LOCKED else current,
                current,
                self._reason(TargetStatus.SUSPENDED),
            )

        target = locked if policy is TargetPolicy.LOCKED else current
        if target is None:
            return TargetDecision(
                TargetStatus.NO_TARGET,
                None,
                current,
                self._reason(TargetStatus.NO_TARGET),
            )

        if policy is TargetPolicy.LOCKED:
            if not self._window_exists(target.hwnd):
                return TargetDecision(
                    TargetStatus.TARGET_CLOSED,
                    target,
                    current,
                    self._reason(TargetStatus.TARGET_CLOSED),
                )
            live = self._window_provider(target.hwnd)
            if live is None:
                return TargetDecision(
                    TargetStatus.TARGET_CLOSED,
                    target,
                    current,
                    self._reason(TargetStatus.TARGET_CLOSED),
                )
            if live.process_id != target.process_id:
                return TargetDecision(
                    TargetStatus.TARGET_REUSED,
                    target,
                    current,
                    self._reason(TargetStatus.TARGET_REUSED),
                )

        blocked = self._blocked_status(target)
        if blocked is not None:
            return TargetDecision(blocked, target, current, self._reason(blocked))

        if current is None or current.identity != target.identity:
            return TargetDecision(
                TargetStatus.TARGET_INACTIVE,
                target,
                current,
                self._reason(TargetStatus.TARGET_INACTIVE),
            )
        return TargetDecision(
            TargetStatus.READY,
            target,
            current,
            self._reason(TargetStatus.READY),
        )

    def try_restore_focus(self) -> bool:
        """Request focus only for a live, identity-checked, safe locked target."""

        with self._lock:
            target = self._locked_target
        if target is None or not self._window_exists(target.hwnd):
            return False
        live = self._window_provider(target.hwnd)
        if live is None or live.process_id != target.process_id:
            return False
        if self._blocked_status(live) is not None:
            return False
        return self._focus_requester(target.hwnd)
