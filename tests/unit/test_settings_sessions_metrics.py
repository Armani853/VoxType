from __future__ import annotations

import json
import time
from pathlib import Path

from voxtype.core.models import Language, ModelProfile
from voxtype.monitoring.metrics import PerformanceMonitor
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.recovery import mark_recovered, recoverable_sessions
from voxtype.sessions.repository import SessionRepository
from voxtype.sessions.retention import cleanup_retention
from voxtype.settings.migration import migrate_settings
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings


def test_settings_round_trip_and_atomic_temp_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    settings = AppSettings()
    settings.general.theme = "dark"
    settings.dictation.language = Language.EN
    repository.save(settings)
    loaded = repository.load()
    assert loaded.general.theme == "dark"
    assert loaded.dictation.language is Language.EN
    assert list(tmp_path.glob("*.tmp")) == []


def test_corrupt_settings_are_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    repository = SettingsRepository(path)
    loaded = repository.load()
    assert loaded.schema_version == 1
    assert repository.last_recovery_backup is not None
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_legacy_safe_clipboard_migrates() -> None:
    migrated = migrate_settings({"schema_version": 0, "dictation": {"safe_clipboard": True}})
    assert migrated["dictation"]["injection_mode"] == "safe_clipboard"


def test_session_journal_is_ordered_idempotent_and_recoverable(tmp_path: Path) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        session_id = repository.create(Language.RU, "small", ModelProfile.FAST, "notepad.exe")
        journal = SessionJournal(repository, session_id)
        assert journal.commit_text("Привет") == 0
        assert journal.append("warning", {"kind": "quiet"}) == 1
        assert repository.commit_text(session_id, 0, "duplicate") is False
        record = repository.get(session_id)
        assert record is not None and record.committed_text == "Привет"
        assert recoverable_sessions(repository)[0].session_id == session_id
        mark_recovered(repository, session_id)
        assert recoverable_sessions(repository) == ()


def test_retention_removes_completed_old_data(tmp_path: Path) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        session_id = repository.create(Language.EN, "small", ModelProfile.FAST, "test")
        repository.finish(session_id)
        with repository._connection:  # deterministic clock setup for this persistence test
            repository._connection.execute(
                "UPDATE sessions SET started_at=? WHERE session_id=?", (1.0, session_id)
            )
        audio_directory = tmp_path / "audio" / session_id
        audio_directory.mkdir(parents=True)
        old = time.time() - 10 * 86_400
        import os

        os.utime(audio_directory, (old, old))
        sessions, directories = cleanup_retention(
            repository, tmp_path / "audio", retention_days=1, now=time.time()
        )
        assert (sessions, directories) == (1, 1)


def test_performance_metrics_are_bounded_and_compute_rtf() -> None:
    monitor = PerformanceMonitor(max_samples=2, max_chunks=2)
    monitor.record_capture(
        480,
        rms=0.1,
        clipping_percentage=0.0,
        queue_size=3,
        queued_seconds=0.09,
    )
    monitor.record_processed(2.0, 1.0, 1.2)
    monitor.sample_resources(queue_size=2, spool_bytes=0)
    snapshot = monitor.snapshot()
    assert snapshot["audio"]["captured_frames"] == 480
    assert snapshot["audio"]["queue_high_watermark"] == 3
    assert snapshot["rtf_average"] == 0.5
