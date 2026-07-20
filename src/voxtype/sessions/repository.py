"""SQLite session storage with WAL, transactions, and no technical-log text leakage."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from voxtype.constants import APP_AUTHOR, APP_NAME
from voxtype.core.models import Language, ModelProfile


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    started_at: float
    finished_at: float | None
    language: str
    model: str
    profile: str
    target_application: str
    committed_text: str
    warnings: tuple[str, ...]
    suspicious_fragments: tuple[str, ...]
    audio_duration_s: float
    processing_s: float
    dropped_frames: int
    status: str


class SessionRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or user_data_path(APP_NAME, APP_AUTHOR) / "sessions.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    language TEXT NOT NULL,
                    model TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    target_application TEXT NOT NULL,
                    committed_text TEXT NOT NULL DEFAULT '',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    suspicious_json TEXT NOT NULL DEFAULT '[]',
                    audio_duration_s REAL NOT NULL DEFAULT 0,
                    processing_s REAL NOT NULL DEFAULT 0,
                    dropped_frames INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_unfinished
                    ON sessions(finished_at, status);
                """
            )

    def create(
        self,
        language: Language,
        model: str,
        profile: ModelProfile,
        target_application: str,
        session_id: str | None = None,
    ) -> str:
        identifier = session_id or str(uuid.uuid4())
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sessions(
                    session_id, started_at, language, model, profile,
                    target_application, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'active')""",
                (
                    identifier,
                    time.time(),
                    language.value,
                    model,
                    profile.value,
                    target_application,
                ),
            )
        return identifier

    def append_event(
        self, session_id: str, sequence: int, event_type: str, payload: dict[str, Any]
    ) -> bool:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT OR IGNORE INTO session_events(
                    session_id, sequence, event_type, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)""",
                (session_id, sequence, event_type, time.time(), encoded),
            )
        return cursor.rowcount == 1

    def commit_text(self, session_id: str, sequence: int, text: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT OR IGNORE INTO session_events(
                    session_id, sequence, event_type, created_at, payload_json
                ) VALUES (?, ?, 'committed_text', ?, ?)""",
                (
                    session_id,
                    sequence,
                    time.time(),
                    json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            if cursor.rowcount == 1:
                self._connection.execute(
                    "UPDATE sessions SET committed_text = committed_text || ? WHERE session_id = ?",
                    (text, session_id),
                )
        return cursor.rowcount == 1

    def update_metrics(
        self,
        session_id: str,
        *,
        audio_duration_s: float,
        processing_s: float,
        dropped_frames: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE sessions SET audio_duration_s=?, processing_s=?, dropped_frames=?
                WHERE session_id=?""",
                (audio_duration_s, processing_s, dropped_frames, session_id),
            )

    def finish(self, session_id: str, status: str = "completed") -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET finished_at=?, status=? WHERE session_id=?",
                (time.time(), status, session_id),
            )

    def add_warning(self, session_id: str, warning: str, suspicious: bool = False) -> None:
        column = "suspicious_json" if suspicious else "warnings_json"
        with self._lock, self._connection:
            row = self._connection.execute(
                f"SELECT {column} FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            values = json.loads(row[column])
            values.append(warning)
            self._connection.execute(
                f"UPDATE sessions SET {column}=? WHERE session_id=?",
                (json.dumps(values, ensure_ascii=False), session_id),
            )

    def get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return self._record(row) if row is not None else None

    def unfinished(self) -> tuple[SessionRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions WHERE finished_at IS NULL ORDER BY started_at"
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def recent(self, limit: int = 100) -> tuple[SessionRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def delete_older_than(self, cutoff_timestamp: float) -> int:
        with self._lock, self._connection:
            session_rows = self._connection.execute(
                "SELECT session_id FROM sessions WHERE started_at < ? AND finished_at IS NOT NULL",
                (cutoff_timestamp,),
            ).fetchall()
            identifiers = [row["session_id"] for row in session_rows]
            for identifier in identifiers:
                self._connection.execute(
                    "DELETE FROM session_events WHERE session_id=?", (identifier,)
                )
                self._connection.execute("DELETE FROM sessions WHERE session_id=?", (identifier,))
        return len(identifiers)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SessionRepository:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            started_at=float(row["started_at"]),
            finished_at=float(row["finished_at"]) if row["finished_at"] is not None else None,
            language=row["language"],
            model=row["model"],
            profile=row["profile"],
            target_application=row["target_application"],
            committed_text=row["committed_text"],
            warnings=tuple(json.loads(row["warnings_json"])),
            suspicious_fragments=tuple(json.loads(row["suspicious_json"])),
            audio_duration_s=float(row["audio_duration_s"]),
            processing_s=float(row["processing_s"]),
            dropped_frames=int(row["dropped_frames"]),
            status=row["status"],
        )

