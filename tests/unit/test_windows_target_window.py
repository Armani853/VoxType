from dataclasses import replace

from voxtype.windows.foreground import IntegrityLevel, WindowMetadata
from voxtype.windows.target_window import (
    TargetPolicy,
    TargetStatus,
    TargetWindowManager,
)


def window(
    hwnd: int,
    pid: int,
    *,
    process: str = "editor.exe",
    integrity: IntegrityLevel = IntegrityLevel.MEDIUM,
) -> WindowMetadata:
    return WindowMetadata(hwnd, f"Window {hwnd}", pid, process, integrity)


class Desktop:
    def __init__(self, foreground: WindowMetadata | None) -> None:
        self.foreground = foreground
        self.windows = {} if foreground is None else {foreground.hwnd: foreground}
        self.focus_requests: list[int] = []

    def capture_foreground(self) -> WindowMetadata | None:
        return self.foreground

    def capture_window(self, hwnd: int) -> WindowMetadata | None:
        return self.windows.get(hwnd)

    def exists(self, hwnd: int) -> bool:
        return hwnd in self.windows

    def request_focus(self, hwnd: int) -> bool:
        self.focus_requests.append(hwnd)
        return self.exists(hwnd)


def manager(desktop: Desktop, **kwargs: object) -> TargetWindowManager:
    return TargetWindowManager(
        own_process_id=999,
        current_integrity=IntegrityLevel.MEDIUM,
        foreground_provider=desktop.capture_foreground,
        window_provider=desktop.capture_window,
        window_exists=desktop.exists,
        focus_requester=desktop.request_focus,
        **kwargs,
    )


def test_locked_target_is_default_and_pauses_on_focus_change() -> None:
    editor = window(10, 100)
    browser = window(20, 200, process="browser.exe")
    desktop = Desktop(editor)
    desktop.windows[browser.hwnd] = browser
    targets = manager(desktop)

    assert targets.start_session().status is TargetStatus.READY
    desktop.foreground = browser
    decision = targets.resolve()

    assert targets.policy is TargetPolicy.LOCKED
    assert decision.status is TargetStatus.TARGET_INACTIVE
    assert decision.target == editor
    assert decision.should_queue


def test_locked_target_recovers_when_user_returns_to_same_identity() -> None:
    editor = window(10, 100)
    desktop = Desktop(editor)
    targets = manager(desktop)
    targets.start_session()
    desktop.foreground = None
    assert targets.resolve().status is TargetStatus.TARGET_INACTIVE
    desktop.foreground = editor
    assert targets.resolve().can_inject


def test_closed_and_reused_hwnd_are_not_treated_as_original_target() -> None:
    editor = window(10, 100)
    desktop = Desktop(editor)
    targets = manager(desktop)
    targets.start_session()

    desktop.windows.clear()
    assert targets.resolve().status is TargetStatus.TARGET_CLOSED

    desktop.windows[10] = replace(editor, process_id=777)
    desktop.foreground = desktop.windows[10]
    assert targets.resolve().status is TargetStatus.TARGET_REUSED


def test_follow_focus_is_explicit_and_tracks_each_foreground_window() -> None:
    editor = window(10, 100)
    browser = window(20, 200, process="browser.exe")
    desktop = Desktop(editor)
    desktop.windows[browser.hwnd] = browser
    targets = manager(desktop, policy=TargetPolicy.FOLLOW_FOCUS)

    assert targets.start_session().target == editor
    desktop.foreground = browser
    decision = targets.resolve()
    assert decision.can_inject
    assert decision.target == browser


def test_own_voxtype_and_higher_integrity_windows_are_blocked() -> None:
    own = window(10, 999, process="python.exe")
    desktop = Desktop(own)
    targets = manager(desktop, policy=TargetPolicy.FOLLOW_FOCUS)
    assert targets.resolve().status is TargetStatus.OWN_WINDOW

    elevated = window(20, 200, integrity=IntegrityLevel.HIGH)
    desktop.foreground = elevated
    desktop.windows[elevated.hwnd] = elevated
    decision = targets.resolve()
    assert decision.status is TargetStatus.HIGHER_INTEGRITY
    assert decision.requires_safe_copy


def test_start_button_retains_last_observed_external_target() -> None:
    editor = window(10, 100)
    own = window(11, 999, process="VoxType.exe")
    desktop = Desktop(editor)
    desktop.windows[own.hwnd] = own
    targets = manager(desktop)

    assert targets.remember_foreground_target() == editor
    desktop.foreground = own
    decision = targets.start_session()

    assert targets.locked_target == editor
    assert decision.status is TargetStatus.TARGET_INACTIVE


def test_start_from_voxtype_without_external_target_never_locks_own_window() -> None:
    own = window(11, 999, process="VoxType.exe")
    desktop = Desktop(own)
    targets = manager(desktop)

    decision = targets.start_session()

    assert targets.locked_target is None
    assert decision.status is TargetStatus.NO_TARGET


def test_unknown_integrity_is_blocked_instead_of_guessing() -> None:
    unknown = window(10, 100, integrity=IntegrityLevel.UNKNOWN)
    desktop = Desktop(unknown)
    targets = manager(desktop, policy=TargetPolicy.FOLLOW_FOCUS)
    assert targets.resolve().status is TargetStatus.INTEGRITY_UNKNOWN


def test_suspend_pauses_injection_until_resume() -> None:
    editor = window(10, 100)
    desktop = Desktop(editor)
    targets = manager(desktop)
    targets.start_session()
    targets.on_suspend()
    assert targets.resolve().status is TargetStatus.SUSPENDED
    targets.on_resume()
    assert targets.resolve().status is TargetStatus.READY


def test_focus_restore_requires_live_safe_original_identity() -> None:
    editor = window(10, 100)
    desktop = Desktop(editor)
    targets = manager(desktop)
    targets.start_session()
    assert targets.try_restore_focus()
    assert desktop.focus_requests == [10]

    desktop.windows[10] = replace(editor, process_id=101)
    assert not targets.try_restore_focus()
