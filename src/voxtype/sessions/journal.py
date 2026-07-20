"""Ordered session journal facade used by the controller."""

from __future__ import annotations

import threading

from voxtype.sessions.repository import SessionRepository


class SessionJournal:
    def __init__(self, repository: SessionRepository, session_id: str):
        self.repository = repository
        self.session_id = session_id
        self._sequence = 0
        self._lock = threading.Lock()

    def append(self, event_type: str, payload: dict[str, object]) -> int:
        with self._lock:
            sequence = self._sequence
            if not self.repository.append_event(self.session_id, sequence, event_type, payload):
                raise RuntimeError(f"Duplicate journal sequence {sequence}")
            self._sequence += 1
            return sequence

    def commit_text(self, text: str) -> int:
        if not text:
            raise ValueError("Cannot journal empty committed text")
        with self._lock:
            sequence = self._sequence
            if not self.repository.commit_text(self.session_id, sequence, text):
                raise RuntimeError(f"Duplicate journal sequence {sequence}")
            self._sequence += 1
            return sequence

