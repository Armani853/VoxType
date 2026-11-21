"""SQLite session storage with WAL, transactions, and no technical-log text leakage."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from voxtype.constants import APP_AUTHOR, APP_NAME, SESSION_SCHEMA_VERSION
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
    profile_id: str = "general"
    source_type: str = "dictation"


@dataclass(frozen=True, slots=True)
class OutboxItem:
    delivery_id: str
    session_id: str
    created_at: float
    text: str
    target_executable: str
    status: str


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    schema_version: int
    session_id: str
    created_at_utc: float
    updated_at_utc: float
    session_type: str
    state: str
    text_snapshot: str
    source_reference: str
    recovery_allowed: bool


class UnsupportedDatabaseSchema(RuntimeError):
    """Newer data should be opened with a compatible application version."""


class SessionRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or user_data_path(APP_NAME, APP_AUTHOR) / "sessions.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.last_recovery_backup: Path | None = None
        self._local = threading.local()
        self._closed = False
        try:
            self._initialize_schema()
        except sqlite3.DatabaseError as exc:
            self._close_thread_connection()
            if not _is_database_corruption(exc):
                raise
            self.last_recovery_backup = self._quarantine_corrupt_database()
            self._initialize_schema()

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._closed:
            raise sqlite3.ProgrammingError("session repository is closed")
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, timeout=3.0)
            try:
                connection.row_factory = sqlite3.Row
                connection.create_function("unicode_casefold", 1, str.casefold, deterministic=True)
                connection.execute("PRAGMA busy_timeout=3000")
                connection.execute("PRAGMA synchronous=FULL")
            except BaseException:
                connection.close()
                raise
            self._local.connection = connection
        return connection

    def _close_thread_connection(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def _initialize_schema(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SESSION_SCHEMA_VERSION:
            raise UnsupportedDatabaseSchema(
                f"Unsupported database schema {version}; expected at most {SESSION_SCHEMA_VERSION}"
            )
        self._connection.execute("PRAGMA journal_mode=WAL")
        with self._connection:
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
                CREATE INDEX IF NOT EXISTS idx_sessions_started_at
                    ON sessions(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_unfinished
                    ON sessions(finished_at, status);
                CREATE TABLE IF NOT EXISTS usage_by_day (
                    utc_day TEXT NOT NULL, profile_id TEXT NOT NULL, outcome TEXT NOT NULL,
                    sessions INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
                    words INTEGER NOT NULL, processing_ms INTEGER NOT NULL,
                    PRIMARY KEY(utc_day, profile_id, outcome)
                );
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, steps_json TEXT NOT NULL
                );
                """
            )
            self._connection.execute("BEGIN IMMEDIATE")
            columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(sessions)")
            }
            if "profile_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE sessions ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'general'"
                )
            if "source_type" not in columns:
                self._connection.execute(
                    "ALTER TABLE sessions ADD COLUMN source_type TEXT NOT NULL DEFAULT 'dictation'"
                )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id, started_at DESC)"
            )
            if version < 1:
                if "recovery_allowed" not in columns:
                    self._connection.execute(
                        "ALTER TABLE sessions ADD COLUMN recovery_allowed INTEGER NOT NULL DEFAULT 0"
                    )
                if "updated_at" not in columns:
                    self._connection.execute("ALTER TABLE sessions ADD COLUMN updated_at REAL")
                self._connection.execute(
                    """UPDATE sessions SET recovery_allowed=1 WHERE status='recovery_required'
                       OR (finished_at IS NULL AND committed_text!='')"""
                )
                self._connection.execute("PRAGMA user_version=1")
            if version < 2:
                self._connection.execute("""
                    CREATE TABLE IF NOT EXISTS delivery_outbox (
                        delivery_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES sessions(session_id),
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        text_start INTEGER NOT NULL,
                        text_length INTEGER NOT NULL,
                        text_sha256 TEXT NOT NULL,
                        target_executable TEXT NOT NULL,
                        requested_strategy TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        last_error_code TEXT
                    )
                """)
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON delivery_outbox(status, created_at)"
                )
                self._connection.execute("""
                    CREATE TRIGGER IF NOT EXISTS session_delete_outbox AFTER DELETE ON sessions
                    BEGIN DELETE FROM delivery_outbox WHERE session_id=OLD.session_id; END
                """)
                self._connection.execute("PRAGMA user_version=2")
            self._initialize_search()

    def _initialize_search(self) -> None:
        existing = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'session_search'"
        ).fetchone()
        triggers = self._connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'session_search_%'"
        ).fetchone()[0]
        try:
            self._connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS session_search
                USING fts5(committed_text, content='sessions', content_rowid='rowid')"""
            )
            self._connection.execute("SELECT rowid FROM session_search LIMIT 0")
        except sqlite3.OperationalError as exc:
            if "no such module" not in str(exc).lower():
                raise
            self._fts = False
            for operation in ("insert", "delete", "update"):
                self._connection.execute(f"DROP TRIGGER IF EXISTS session_search_{operation}")
            return
        self._fts = True
        self._connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS session_search_insert AFTER INSERT ON sessions
            BEGIN
                INSERT INTO session_search(rowid, committed_text) VALUES(new.rowid, new.committed_text);
            END;
            CREATE TRIGGER IF NOT EXISTS session_search_delete AFTER DELETE ON sessions
            BEGIN
                INSERT INTO session_search(session_search, rowid, committed_text)
                    VALUES('delete', old.rowid, old.committed_text);
            END;
            CREATE TRIGGER IF NOT EXISTS session_search_update AFTER UPDATE OF committed_text ON sessions
            BEGIN
                INSERT INTO session_search(session_search, rowid, committed_text)
                    VALUES('delete', old.rowid, old.committed_text);
                INSERT INTO session_search(rowid, committed_text) VALUES(new.rowid, new.committed_text);
            END;
            """
        )
        if existing is None or triggers != 3:
            self._connection.execute("INSERT INTO session_search(session_search) VALUES('rebuild')")

    def _quarantine_corrupt_database(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        backup = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}{self.path.suffix}")
        counter = 0
        while backup.exists():
            counter += 1
            backup = self.path.with_name(
                f"{self.path.stem}.corrupt-{stamp}-{counter}{self.path.suffix}"
            )
        if self.path.exists():
            os.replace(self.path, backup)
        for suffix in ("-wal", "-shm"):
            companion = self.path.with_name(self.path.name + suffix)
            if companion.exists():
                os.replace(companion, backup.with_name(backup.name + suffix))
        return backup

    def create(
        self,
        language: Language,
        model: str,
        profile: ModelProfile,
        target_application: str,
        session_id: str | None = None,
        *,
        profile_id: str = "general",
        source_type: str = "dictation",
    ) -> str:
        identifier = session_id or str(uuid.uuid4())
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sessions(
                    session_id, started_at, language, model, profile,
                    target_application, profile_id, source_type, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                (
                    identifier,
                    time.time(),
                    language.value,
                    model,
                    profile.value,
                    target_application,
                    profile_id,
                    source_type,
                ),
            )
        return identifier

    def save_file_record(
        self,
        session_id: str,
        language: str,
        model: str,
        profile_id: str,
        model_profile: str,
        duration: float,
        text: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sessions
                   (session_id, started_at, finished_at, language, model, profile, profile_id,
                    target_application, source_type, committed_text, audio_duration_s, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', 'file', ?, ?, 'completed')
                   ON CONFLICT(session_id) DO NOTHING""",
                (session_id, now, now, language, model, model_profile, profile_id, text, duration),
            )

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

    def queue_delivery(
        self,
        delivery_id: str,
        session_id: str,
        text: str,
        strategy: str,
        *,
        whole_result: bool,
    ) -> bool:
        if not text:
            return False
        digest = sha256(text.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT committed_text, target_application FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                return False
            committed = row["committed_text"]
            start = 0 if whole_result else len(committed) - len(text)
            if start < 0 or committed[start : start + len(text)] != text:
                return False
            cursor = self._connection.execute(
                """
                INSERT INTO delivery_outbox (
                    delivery_id, session_id, created_at, updated_at, text_start,
                    text_length, text_sha256, target_executable, requested_strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_id) DO NOTHING
            """,
                (
                    delivery_id,
                    session_id,
                    now,
                    now,
                    start,
                    len(text),
                    digest,
                    row["target_application"],
                    strategy,
                ),
            )
            return cursor.rowcount == 1

    def claim_delivery(self, delivery_id: str, *, explicit: bool = False) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE delivery_outbox SET status='attempting', updated_at=?, attempt_count=attempt_count+1
                WHERE delivery_id=? AND (status='pending' OR (? AND status IN (
                    'failed', 'submitted_unconfirmed'
                )))
            """,
                (time.time(), delivery_id, explicit),
            )
            return cursor.rowcount == 1

    def finish_delivery(self, delivery_id: str, status: str, error: str | None = None) -> None:
        if status not in {"submitted_unconfirmed", "confirmed", "failed", "cancelled"}:
            raise ValueError("Unsupported delivery status")
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE delivery_outbox SET status=?, updated_at=?, last_error_code=?
                   WHERE delivery_id=? AND (status='attempting' OR (?='confirmed'
                   AND status IN ('submitted_unconfirmed','attempting','failed')) OR (?='cancelled' AND status='pending'))""",
                (status, time.time(), error, delivery_id, status, status),
            )

    def queued_retry_ids(self, session_id: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT delivery_id FROM delivery_outbox
                WHERE session_id=? AND status='failed' AND last_error_code IN ('queued','blocked')
                ORDER BY created_at DESC LIMIT 256
            """,
                (session_id,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def settle_queued_retry(self, delivery_ids: tuple[str, ...]) -> None:
        with self._lock, self._connection:
            self._connection.executemany(
                """
                UPDATE delivery_outbox SET status='submitted_unconfirmed', updated_at=?, last_error_code=NULL
                WHERE delivery_id=? AND status='failed' AND last_error_code IN ('queued','blocked')
            """,
                ((time.time(), delivery_id) for delivery_id in delivery_ids),
            )

    def pending_deliveries(self, limit: int = 40) -> tuple[OutboxItem, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT d.*, substr(s.committed_text, d.text_start + 1, d.text_length) AS fragment
                FROM delivery_outbox d JOIN sessions s USING(session_id)
                WHERE d.status NOT IN ('confirmed','cancelled')
                ORDER BY d.created_at DESC LIMIT ?
            """,
                (max(1, min(100, limit)),),
            ).fetchall()
        return tuple(
            OutboxItem(
                row["delivery_id"],
                row["session_id"],
                row["created_at"],
                row["fragment"],
                row["target_executable"],
                row["status"],
            )
            for row in rows
            if sha256(row["fragment"].encode("utf-8")).hexdigest() == row["text_sha256"]
        )

    def delete_delivery(self, delivery_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM delivery_outbox WHERE delivery_id=?", (delivery_id,)
            )

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

    def require_recovery(self, session_id: str) -> None:
        """Keep a failed session unfinished so startup can offer PCM recovery."""

        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET finished_at=NULL, status='recovery_required' WHERE session_id=?",
                (session_id,),
            )

    def commit_next_text(self, session_id: str, text: str) -> int:
        """Append recovered text using the next durable journal sequence."""

        if not text:
            raise ValueError("Cannot journal empty committed text")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 AS sequence FROM session_events WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            sequence = int(row["sequence"])
            self._connection.execute(
                """INSERT INTO session_events(
                    session_id, sequence, event_type, created_at, payload_json
                ) VALUES (?, ?, 'recovered_text', ?, ?)""",
                (
                    session_id,
                    sequence,
                    time.time(),
                    json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            cursor = self._connection.execute(
                "UPDATE sessions SET committed_text=committed_text || ? WHERE session_id=?",
                (text, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
        return sequence

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

    def mark_recoverable(self, session_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET recovery_allowed=1, updated_at=? WHERE session_id=?",
                (time.time(), session_id),
            )

    def save_draft(self, session_id: str, text: str, *, limit: int = 20) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE sessions SET committed_text=?, source_type='draft', recovery_allowed=1, updated_at=?
                   WHERE session_id=?""",
                (text, time.time(), session_id),
            )
            self._prune_drafts(limit)

    def save_editor_draft(self, session_id: str, language: str, text: str, touched: float) -> None:
        if language not in ("ru", "en") or not text.strip():
            return
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sessions(session_id, started_at, finished_at, language, model, profile,
                target_application, source_type, status, committed_text, recovery_allowed, updated_at)
                VALUES (?, ?, ?, ?, '', '', '', 'draft', 'draft', ?, 1, ?)
                ON CONFLICT(session_id) DO UPDATE SET committed_text=excluded.committed_text,
                    finished_at=excluded.finished_at, updated_at=excluded.updated_at
                WHERE sessions.source_type='draft' AND sessions.updated_at <= excluded.updated_at""",
                (session_id, touched, touched, language, text, touched),
            )
            self._prune_drafts(20)

    def _prune_drafts(self, limit: int) -> None:
        self._connection.execute(
            """DELETE FROM session_events WHERE session_id IN (
               SELECT session_id FROM sessions WHERE source_type='draft' AND finished_at IS NOT NULL
               ORDER BY updated_at DESC LIMIT -1 OFFSET ?)""",
            (limit,),
        )
        self._connection.execute(
            """DELETE FROM sessions WHERE session_id IN (
               SELECT session_id FROM sessions WHERE source_type='draft' AND finished_at IS NOT NULL
               ORDER BY updated_at DESC LIMIT -1 OFFSET ?)""",
            (limit,),
        )

    def recoverable(self, *, include_drafts: bool, limit: int = 100) -> tuple[RecoveryRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT session_id, started_at, COALESCE(updated_at, finished_at, started_at) AS touched,
                   source_type, status, committed_text FROM sessions
                   WHERE recovery_allowed=1 AND (source_type!='draft' OR ?)
                     AND (finished_at IS NULL OR status IN ('interrupted','recovery_required') OR source_type='draft')
                   ORDER BY touched DESC LIMIT ?""",
                (include_drafts, max(1, min(100, limit))),
            ).fetchall()
        return tuple(
            RecoveryRecord(
                1,
                row["session_id"],
                row["started_at"],
                row["touched"],
                row["source_type"],
                row["status"],
                row["committed_text"],
                row["session_id"],
                True,
            )
            for row in rows
        )

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

    def search(
        self,
        *,
        text: str = "",
        profile: str = "",
        application: str = "",
        since: float | None = None,
        until: float | None = None,
        page: int = 0,
        language: str = "",
        status: str = "",
        include_drafts: bool = False,
        source_type: str = "",
    ) -> tuple[tuple[SessionRecord, ...], bool]:
        clauses: list[str] = [] if include_drafts else ["source_type != 'draft'"]
        values: list[str | float | int] = []
        text = text.strip()[:256]
        if text and self._fts and any(char.isalnum() for char in text):
            clauses.append(
                "rowid IN (SELECT rowid FROM session_search WHERE session_search MATCH ?)"
            )
            values.append('"' + text.replace('"', '""') + '"')
        elif text:
            clauses.append("unicode_casefold(committed_text) LIKE ? ESCAPE '!'")
            escaped = text.casefold().replace("!", "!!").replace("%", "!%").replace("_", "!_")
            values.append(f"%{escaped}%")
        if profile:
            clauses.append("profile_id = ?")
            values.append(profile)
        if application:
            clauses.append("instr(lower(target_application), lower(?)) > 0")
            values.append(application)
        if language:
            clauses.append("language = ?")
            values.append(language)
        if source_type:
            clauses.append("source_type = ?")
            values.append(source_type)
        if status == "incomplete":
            clauses.append("status NOT IN ('completed', 'recovered')")
        elif status:
            clauses.append("status = ?")
            values.append(status)
        if since is not None:
            clauses.append("started_at >= ?")
            values.append(since)
        if until is not None:
            clauses.append("started_at < ?")
            values.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM sessions{where} ORDER BY started_at DESC, rowid DESC LIMIT 101 OFFSET ?",
                (*values, max(0, page) * 100),
            ).fetchall()
        return tuple(self._record(row) for row in rows[:100]), len(rows) > 100

    def record_usage(
        self,
        profile_id: str,
        outcome: str,
        duration_ms: int,
        words: int,
        processing_ms: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO usage_by_day VALUES (date('now'), ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(utc_day, profile_id, outcome) DO UPDATE SET
                       sessions = sessions + 1, duration_ms = duration_ms + excluded.duration_ms,
                       words = words + excluded.words, processing_ms = processing_ms + excluded.processing_ms""",
                (profile_id, outcome, duration_ms, words, processing_ms),
            )

    def usage_summary(self, *, weekly: bool) -> tuple[dict[str, Any], ...]:
        period = "date(utc_day, 'weekday 0', '-6 days')" if weekly else "utc_day"
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT {period} AS period, profile_id, outcome, SUM(sessions) AS sessions,
                           SUM(duration_ms) AS duration_ms, SUM(words) AS words,
                           SUM(processing_ms) AS processing_ms FROM usage_by_day
                    WHERE utc_day >= date('now', '-90 days')
                    GROUP BY period, profile_id, outcome ORDER BY period DESC, profile_id, outcome"""
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def save_workflow_steps(self, steps: list[dict[str, str]]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO workflow_runs(steps_json) VALUES (?)", (json.dumps(steps),)
            )
            self._connection.execute(
                """DELETE FROM workflow_runs WHERE id IN (
                       SELECT id FROM workflow_runs ORDER BY id DESC LIMIT -1 OFFSET 100
                   )"""
            )

    def latest_workflow_steps(self) -> list[dict[str, str]]:
        with self._lock:
            record = self._connection.execute(
                "SELECT steps_json FROM workflow_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return json.loads(record["steps_json"]) if record else []

    def language_switches(self, session_id: str) -> tuple[tuple[float, str], ...]:
        """Return durable language changes for replaying an unfinished spool."""

        with self._lock:
            rows = self._connection.execute(
                """SELECT payload_json FROM session_events
                WHERE session_id=? AND event_type='language_switch' ORDER BY sequence""",
                (session_id,),
            ).fetchall()
        switches: list[tuple[float, str]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            language = payload.get("language")
            captured_at = payload.get("captured_at")
            if language in {"ru", "en"} and isinstance(captured_at, int | float):
                switches.append((float(captured_at), str(language)))
        return tuple(switches)

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

    def delete(self, session_id: str) -> bool:
        """Delete one session and its event journal atomically."""

        with self._lock, self._connection:
            self._connection.execute("DELETE FROM session_events WHERE session_id=?", (session_id,))
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE session_id=?", (session_id,)
            )
        return cursor.rowcount == 1

    def delete_all_finished(self) -> int:
        """Remove local history without touching active recovery journals."""

        with self._lock, self._connection:
            condition = "finished_at IS NOT NULL AND status NOT IN ('active', 'recovery_required')"
            self._connection.execute(
                f"DELETE FROM session_events WHERE session_id IN (SELECT session_id FROM sessions WHERE {condition})"
            )
            return self._connection.execute(f"DELETE FROM sessions WHERE {condition}").rowcount

    def delete_all(self) -> int:
        """Erase every transcript and journal after capture has stopped."""

        with self._lock, self._connection:
            row = self._connection.execute("SELECT COUNT(*) AS total FROM sessions").fetchone()
            total = int(row["total"]) if row is not None else 0
            self._connection.execute("DELETE FROM session_events")
            self._connection.execute("DELETE FROM sessions")
            self._connection.execute("DELETE FROM usage_by_day")
            self._connection.execute("DELETE FROM workflow_runs")
            if self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'transcription_jobs'"
            ).fetchone():
                self._connection.execute("DELETE FROM transcription_jobs")
                self._connection.execute("UPDATE transcription_queue_state SET paused = 0")
        return total

    def close_current_thread(self) -> None:
        with self._lock:
            self._close_thread_connection()

    def close(self) -> None:
        with self._lock:
            self._close_thread_connection()
            self._closed = True

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
            profile_id=row["profile_id"],
            source_type=row["source_type"],
        )


def _is_database_corruption(error: sqlite3.DatabaseError) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "database disk image is malformed",
            "file is not a database",
            "file is encrypted",
            "malformed database schema",
        )
    )
