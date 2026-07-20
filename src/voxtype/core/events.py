"""Bounded in-process event bus used to decouple workers from the UI."""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    AUDIO_LEVEL = "audio_level"
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"
    PROVISIONAL_TEXT = "provisional_text"
    COMMITTED_TEXT = "committed_text"
    BACKLOG_CHANGED = "backlog_changed"
    TARGET_CHANGED = "target_changed"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AppEvent:
    kind: EventKind
    payload: Any = None
    created_at: float = field(default_factory=time.monotonic)


class EventBus:
    """A bounded queue; high-rate level events may be coalesced by producers."""

    def __init__(self, max_events: int = 1024):
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._queue: queue.Queue[AppEvent] = queue.Queue(maxsize=max_events)

    def publish(self, event: AppEvent, timeout_s: float = 0.05) -> bool:
        try:
            self._queue.put(event, timeout=timeout_s)
        except queue.Full:
            return False
        return True

    def next(self, timeout_s: float = 0.1) -> AppEvent | None:
        try:
            return self._queue.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        self._queue.task_done()

    @property
    def size(self) -> int:
        return self._queue.qsize()
