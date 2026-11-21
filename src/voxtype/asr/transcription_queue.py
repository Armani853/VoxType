"""Disk-backed queue for explicitly requested local file transcription."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

from voxtype.asr.file_transcription import (
    EXPORT_EXTENSIONS,
    FileTranscript,
    TranscriptionCanceled,
    export_transcript,
    validate_media,
)
from voxtype.windows.local_paths import is_local_path


@dataclass(frozen=True, slots=True)
class TranscriptionJob:
    id: str
    input_path: str
    output_path: str
    language: str
    profile_id: str
    priority: int
    status: str
    created_at: float
    started_at: float | None
    finished_at: float | None
    error: str


class FileTranscriptionQueue:
    def __init__(
        self,
        path: Path,
        controller,
        *,
        changed: Callable[[], None] | None = None,
    ) -> None:
        self.path = path
        self.controller = controller
        self._worker: Thread | None = None
        self._cancel: Event | None = None
        self._request_shutdown = False
        self._changed = changed
        self._revision_lock = Lock()
        self.active_id: str | None = None
        self.revision = time.monotonic_ns()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS transcription_jobs (
                    id TEXT PRIMARY KEY, input_path TEXT NOT NULL, output_path TEXT NOT NULL,
                    language TEXT NOT NULL, profile_id TEXT NOT NULL, priority INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL,
                    finished_at REAL, error TEXT NOT NULL DEFAULT '', result TEXT
                );
                CREATE INDEX IF NOT EXISTS transcription_jobs_order
                    ON transcription_jobs(status, priority DESC, created_at);
                CREATE TABLE IF NOT EXISTS transcription_queue_state (paused INTEGER NOT NULL);
                INSERT INTO transcription_queue_state SELECT 0
                    WHERE NOT EXISTS (SELECT 1 FROM transcription_queue_state);
                UPDATE transcription_jobs SET status = 'QUEUED', started_at = NULL
                    WHERE status = 'RUNNING';
            """)
            self.paused = bool(
                db.execute("SELECT paused FROM transcription_queue_state").fetchone()[0]
            )
            self._has_pending = (
                db.execute(
                    "SELECT 1 FROM transcription_jobs WHERE status = 'QUEUED' LIMIT 1"
                ).fetchone()
                is not None
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(
        self,
        source: Path,
        destination: Path | None,
        language: str,
        profile_id: str,
        priority: int,
    ) -> None:
        self._check_policy_paths(source, destination)
        source = validate_media(source)
        if destination is not None:
            destination = destination.resolve()
            if destination.suffix.lower() not in EXPORT_EXTENSIONS:
                raise ValueError("Неподдерживаемый формат экспорта")
            if source == destination or destination.exists():
                raise ValueError("Выберите новый путь экспорта, отличный от исходного файла")
        with self._connect() as db:
            db.execute(
                """INSERT INTO transcription_jobs
                    (id, input_path, output_path, language, profile_id, priority, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?)""",
                (
                    uuid4().hex,
                    str(source),
                    str(destination) if destination else "",
                    language,
                    profile_id,
                    priority,
                    time.time(),
                ),
            )
        self._publish_change(pending=True)

    def _check_policy_paths(self, source: Path, destination: Path | None) -> None:
        if self.controller.policy.enforce_offline_only:
            for filename in (source, destination):
                if filename is not None and (
                    not is_local_path(filename.absolute()) or not is_local_path(filename.resolve())
                ):
                    raise ValueError("Политика организации разрешает только локальные файлы")

    def set_paused(self, paused: bool) -> None:
        with self._connect() as db:
            db.execute("UPDATE transcription_queue_state SET paused = ?", (int(paused),))
        self.paused = paused
        self._publish_change()

    def cancel(self, job_id: str, *, active: bool = False) -> None:
        cancel = self._cancel
        if active and cancel is not None and job_id == self.active_id:
            cancel.set()
            return
        with self._connect() as db:
            db.execute(
                """UPDATE transcription_jobs SET status = 'CANCELED', finished_at = ?
                          WHERE id = ? AND status = 'QUEUED'""",
                (time.time(), job_id),
            )
        self._publish_change()

    def cancel_pending(self) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE transcription_jobs SET status = 'CANCELED', finished_at = ?
                          WHERE status = 'QUEUED'""",
                (time.time(),),
            )
        self._publish_change()

    def begin_shutdown(self) -> None:
        self._request_shutdown = True
        cancel = self._cancel
        if cancel is not None:
            cancel.set()

    def wait_for_worker(self, timeout: float) -> None:
        if self._worker is not None:
            self._worker.join(timeout)

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @staticmethod
    def _job(row: sqlite3.Row) -> TranscriptionJob:
        return TranscriptionJob(**{key: row[key] for key in row.keys() if key != "result"})  # noqa: SIM118 (sqlite3.Row iterates values)

    def recent(self) -> tuple[TranscriptionJob, ...]:
        with self._connect() as db:
            return tuple(
                self._job(row)
                for row in db.execute(
                    """SELECT id, input_path, output_path, language, profile_id, priority,
                          status, created_at, started_at, finished_at, error FROM transcription_jobs
                   ORDER BY CASE status WHEN 'RUNNING' THEN 0 WHEN 'QUEUED' THEN 1 ELSE 2 END,
                            priority DESC, created_at DESC LIMIT 100"""
                )
            )

    def get_result(self, job_id: str) -> FileTranscript | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT result FROM transcription_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return FileTranscript.from_json(row["result"]) if row and row["result"] else None

    def save_result(self, job_id: str, transcript: FileTranscript) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE transcription_jobs SET result = ?
                          WHERE id = ? AND result IS NOT NULL""",
                (transcript.json(), job_id),
            )

    def _publish_change(self, *, pending: bool = False) -> None:
        with self._revision_lock:
            if pending:
                self._has_pending = True
            self.revision = time.monotonic_ns()
        if self._changed is not None:
            self._changed()

    @property
    def needs_tick(self) -> bool:
        return not self._request_shutdown and (self.busy or (self._has_pending and not self.paused))

    def tick(self) -> None:
        if self.paused or self._request_shutdown or self.busy or not self._has_pending:
            return
        cancel = self.controller.reserve_file_recognition()
        if cancel is None:
            return
        self._cancel = cancel
        self._worker = Thread(
            target=self._claim_and_run, args=(cancel,), name="voxtype-file-asr", daemon=True
        )
        try:
            self._worker.start()
        except BaseException:
            self.controller.release_file_recognition()
            raise

    def _claim_and_run(self, cancel: Event) -> None:
        try:
            with self._revision_lock:
                revision = self.revision
            if self.paused or cancel.is_set():
                return
            with self._connect() as db:
                # Atomically claim a queued row. Pause/cancel from the UI may
                # have changed its status between the initial check and now.
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("""SELECT * FROM transcription_jobs WHERE status = 'QUEUED'
                                    ORDER BY priority DESC, created_at, rowid LIMIT 1""").fetchone()
                if row is not None:
                    job = self._job(row)
                    db.execute(
                        "UPDATE transcription_jobs SET status = 'RUNNING', started_at = ? WHERE id = ?",
                        (time.time(), job.id),
                    )
            if row is None:
                with self._revision_lock:
                    if self.revision == revision:
                        self._has_pending = False
                return
            self.active_id = job.id
            self._publish_change()
            self._run(job, cancel)
        except sqlite3.Error:
            # Keep any persisted RUNNING row recoverable on the next startup.
            self.paused = True
        finally:
            self.active_id = None
            self._cancel = None
            self.controller.release_file_recognition()
            self._publish_change()

    def _run(self, job: TranscriptionJob, cancel: Event) -> None:
        result = None
        error = ""
        try:
            try:
                self._check_policy_paths(
                    Path(job.input_path), Path(job.output_path) if job.output_path else None
                )
                transcript = self.controller.transcribe_professional_file(
                    Path(job.input_path),
                    job.profile_id,
                    job.language,
                    job.id,
                )
                if cancel.is_set():
                    raise TranscriptionCanceled()
                result = transcript.json()
                self.controller.journal_file_result(
                    job.id,
                    job.profile_id,
                    transcript.language,
                    transcript.duration,
                    transcript.text,
                )
                if job.output_path:
                    export_transcript(
                        transcript, Path(job.output_path), source=Path(job.input_path)
                    )
                workflow = getattr(self.controller, "workflows", None)
                if workflow is not None:
                    workflow.dispatch("file", job.profile_id, transcript.text)
                status = "COMPLETED"
                if not transcript.segments:
                    error = "Речь не распознана"
            except TranscriptionCanceled:
                status = "QUEUED" if self._request_shutdown else "CANCELED"
            except ImportError:
                status, error = "FAILED", "Не установлен декодер или компонент экспорта"
            except FileExistsError:
                status, error = "FAILED", "Выходной путь уже занят. Текст доступен в редакторе."
            except Exception as exc:
                status, error = "FAILED", f"Ошибка транскрипции или экспорта ({type(exc).__name__})"
            with self._connect() as db:
                db.execute(
                    """UPDATE transcription_jobs SET status = ?, finished_at = ?, error = ?, result = ?
                       WHERE id = ?""",
                    (status, None if status == "QUEUED" else time.time(), error, result, job.id),
                )
        except sqlite3.Error:
            # Keep the persisted RUNNING row recoverable on the next startup.
            self.paused = True
