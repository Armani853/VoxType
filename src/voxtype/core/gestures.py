"""Dictation gestures sharing the application's existing session lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voxtype.core.controller import ApplicationController


class DictationMode(StrEnum):
    PUSH_TO_TALK = "push_to_talk"
    TOGGLE = "toggle"
    AUTO_STOP = "auto_stop"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class SilenceAutoStopSettings:
    enabled: bool = True
    minimum_speech_ms: int = 400
    trailing_silence_ms: int = 1500
    max_session_duration_ms: int = 120_000

    @property
    def duration_limit_ms(self) -> int:
        return min(3_600_000, max(1000, self.max_session_duration_ms))

    def reached(self, elapsed_ms: float, speech_ms: float, silence_ms: float) -> bool:
        if not self.enabled:
            return False
        return elapsed_ms >= self.duration_limit_ms or (
            speech_ms >= max(100, self.minimum_speech_ms)
            and silence_ms >= max(200, self.trailing_silence_ms)
        )


class DictationGestureController:
    def __init__(self, controller: ApplicationController) -> None:
        self._controller = controller
        self._hold_session: int | None = None

    def hold_changed(self, pressed: bool) -> None:
        if pressed:
            if self._hold_session is None:
                self._hold_session = self._controller.start_hold_dictation()
        else:
            session, self._hold_session = self._hold_session, None
            if session is not None:
                self._controller.stop_hold_dictation(session)
