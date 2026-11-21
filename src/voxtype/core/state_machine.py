"""Thread-safe finite state machine for the application lifecycle."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from voxtype.core.exceptions import InvalidStateTransition


class AppState(StrEnum):
    STARTING = "starting"
    MODEL_LOADING = "model_loading"
    IDLE = "idle"
    LISTENING = "listening"
    SPEECH_DETECTED = "speech_detected"
    TRANSCRIBING = "transcribing"
    PAUSED = "paused"
    PAUSED_TARGET_LOST = "paused_target_lost"
    FINISHING = "finishing"
    COMPLETED = "completed"
    ERROR_RECOVERABLE = "error_recoverable"
    ERROR_FATAL = "error_fatal"
    SHUTTING_DOWN = "shutting_down"


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: AppState
    current: AppState
    reason: str
    monotonic_time: float


_COMMON_ACTIVE = {
    AppState.LISTENING,
    AppState.SPEECH_DETECTED,
    AppState.TRANSCRIBING,
    AppState.PAUSED,
    AppState.PAUSED_TARGET_LOST,
    AppState.FINISHING,
    AppState.ERROR_RECOVERABLE,
    AppState.ERROR_FATAL,
    AppState.SHUTTING_DOWN,
}

_ALLOWED: dict[AppState, frozenset[AppState]] = {
    AppState.STARTING: frozenset(
        {
            AppState.MODEL_LOADING,
            AppState.IDLE,
            AppState.FINISHING,
            AppState.ERROR_RECOVERABLE,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }
    ),
    AppState.MODEL_LOADING: frozenset(
        {
            AppState.IDLE,
            AppState.FINISHING,
            AppState.ERROR_RECOVERABLE,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }
    ),
    AppState.IDLE: frozenset(
        {
            AppState.MODEL_LOADING,
            AppState.STARTING,
            AppState.LISTENING,
            AppState.ERROR_RECOVERABLE,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }
    ),
    AppState.LISTENING: frozenset(_COMMON_ACTIVE - {AppState.LISTENING}),
    AppState.SPEECH_DETECTED: frozenset(_COMMON_ACTIVE - {AppState.SPEECH_DETECTED}),
    AppState.TRANSCRIBING: frozenset(_COMMON_ACTIVE - {AppState.TRANSCRIBING}),
    AppState.PAUSED: frozenset(_COMMON_ACTIVE - {AppState.PAUSED}),
    AppState.PAUSED_TARGET_LOST: frozenset(_COMMON_ACTIVE - {AppState.PAUSED_TARGET_LOST}),
    AppState.FINISHING: frozenset(
        {
            AppState.IDLE,
            AppState.COMPLETED,
            AppState.ERROR_RECOVERABLE,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }
    ),
    AppState.COMPLETED: frozenset(
        {AppState.STARTING, AppState.IDLE, AppState.ERROR_FATAL, AppState.SHUTTING_DOWN}
    ),
    AppState.ERROR_RECOVERABLE: frozenset(
        {
            AppState.IDLE,
            AppState.STARTING,
            AppState.LISTENING,
            AppState.FINISHING,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }
    ),
    AppState.ERROR_FATAL: frozenset({AppState.SHUTTING_DOWN}),
    AppState.SHUTTING_DOWN: frozenset(),
}


class StateMachine:
    """Serialize state transitions and notify observers after committing them."""

    def __init__(self, initial: AppState = AppState.STARTING, logger: logging.Logger | None = None):
        self._state = initial
        self._logger = logger or logging.getLogger("voxtype.state")
        self._lock = threading.RLock()
        self._listeners: list[Callable[[StateTransition], None]] = []
        self._history: deque[StateTransition] = deque(maxlen=512)

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    @property
    def history(self) -> tuple[StateTransition, ...]:
        with self._lock:
            return tuple(self._history)

    def subscribe(self, listener: Callable[[StateTransition], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def can_transition(self, target: AppState) -> bool:
        with self._lock:
            return target in _ALLOWED[self._state]

    def transition(self, target: AppState, reason: str) -> StateTransition:
        if not reason.strip():
            raise ValueError("A state transition requires a non-empty reason")
        with self._lock:
            previous = self._state
            if target == previous:
                raise InvalidStateTransition(f"State is already {target.value}")
            if target not in _ALLOWED[previous]:
                raise InvalidStateTransition(
                    f"Forbidden transition {previous.value} -> {target.value}"
                )
            transition = StateTransition(previous, target, reason, time.monotonic())
            self._state = target
            self._history.append(transition)
            listeners = tuple(self._listeners)
        self._logger.info(
            "state_transition previous=%s current=%s reason=%s",
            previous.value,
            target.value,
            reason,
        )
        for listener in listeners:
            try:
                listener(transition)
            except Exception:
                self._logger.exception("state_listener_failed current=%s", target.value)
        return transition

    def transition_if(
        self, expected: set[AppState] | frozenset[AppState], target: AppState, reason: str
    ) -> StateTransition | None:
        """Atomically transition only while the state is one of ``expected``."""

        if not reason.strip():
            raise ValueError("A state transition requires a non-empty reason")
        with self._lock:
            if self._state not in expected:
                return None
            previous = self._state
            if target not in _ALLOWED[previous]:
                raise InvalidStateTransition(
                    f"Forbidden transition {previous.value} -> {target.value}"
                )
            transition = StateTransition(previous, target, reason, time.monotonic())
            self._state = target
            self._history.append(transition)
            listeners = tuple(self._listeners)
        self._logger.info(
            "state_transition previous=%s current=%s reason=%s",
            previous.value,
            target.value,
            reason,
        )
        for listener in listeners:
            try:
                listener(transition)
            except Exception:
                self._logger.exception("state_listener_failed current=%s", target.value)
        return transition
