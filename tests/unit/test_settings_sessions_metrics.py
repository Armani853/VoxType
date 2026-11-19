from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS
from voxtype.asr.profiles import ASRProfile, get_profile
from voxtype.core.models import InjectionMode, Language, ModelProfile, PreprocessingProfile
from voxtype.monitoring.metrics import PerformanceMonitor
from voxtype.runtime import (
    cache_directory_path,
    configured_profile,
    data_directory_path,
    effective_cpu_threads,
)
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


def test_power_modes_change_effective_cpu_budget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("voxtype.runtime.os.cpu_count", lambda: 12)
    settings = AppSettings()
    settings.model.cpu_threads = 8
    settings.model.power_mode = "energy"
    assert effective_cpu_threads(settings) == 4
    settings.model.power_mode = "balanced"
    assert effective_cpu_threads(settings) == 8
    settings.model.power_mode = "maximum"
    assert effective_cpu_threads(settings) == 12


def test_default_injection_uses_clipboard_with_unicode_fallback() -> None:
    settings = AppSettings()
    assert settings.dictation.injection_mode is InjectionMode.AUTO
    assert settings.dictation.model_profile is ModelProfile.BALANCED
    assert settings.model.installed_model == "small"
    assert settings.audio.preprocessing_profile is PreprocessingProfile.RAW
    assert not settings.audio.normalization


def test_acceptance_profile_directories_are_explicitly_isolated(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    data = tmp_path / "fresh-data"
    cache = tmp_path / "fresh-cache"
    monkeypatch.setenv("VOXTYPE_DATA_DIRECTORY", str(data))
    monkeypatch.setenv("VOXTYPE_CACHE_DIRECTORY", str(cache))
    assert data_directory_path() == data.resolve()
    assert cache_directory_path() == cache.resolve()
    monkeypatch.delenv("VOXTYPE_CACHE_DIRECTORY")
    assert cache_directory_path() == data.resolve() / "cache"


def test_measured_profiles_choose_small_balanced_and_turbo_maximum() -> None:
    settings = AppSettings()
    settings.dictation.model_profile = ModelProfile.BALANCED
    settings.model.installed_model = "stale-large-v3"

    assert configured_profile(settings).model_name == "small"
    assert get_profile(ASRProfile.MAXIMUM_ACCURACY).model_name == "large-v3-turbo"
    assert DEFAULT_MODEL_SPECS[ASRProfile.BALANCED].model_id == "small"
    assert DEFAULT_MODEL_SPECS[ASRProfile.MAXIMUM_ACCURACY].model_id == "large-v3-turbo"


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


def test_session_journal_append_sequence_is_contiguous(tmp_path: Path) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        session_id = repository.create(Language.EN, "small", ModelProfile.FAST, "notepad.exe")
        journal = SessionJournal(repository, session_id)
        assert journal.append("first", {"value": 1}) == 0
        assert journal.append("second", {"value": 2}) == 1


def test_session_journal_rejects_empty_and_duplicate_commits(tmp_path: Path) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        session_id = repository.create(Language.RU, "small", ModelProfile.FAST, "notepad.exe")
        journal = SessionJournal(repository, session_id)
        with pytest.raises(ValueError):
            journal.commit_text("")
        assert repository.commit_text(session_id, 0, "already committed")
        with pytest.raises(RuntimeError, match="Duplicate journal sequence 0"):
            journal.commit_text("must not overwrite")
        record = repository.get(session_id)
        assert record is not None and record.committed_text == "already committed"


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


def test_retention_removes_only_orphaned_old_spool(tmp_path: Path) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        session_id = repository.create(Language.RU, "small", ModelProfile.FAST, "test")
        spool_root = tmp_path / "spool"
        owned = spool_root / session_id
        orphan = spool_root / "orphan"
        owned.mkdir(parents=True)
        orphan.mkdir()
        old = time.time() - 10 * 86_400
        import os

        os.utime(owned, (old, old))
        os.utime(orphan, (old, old))
        _sessions, directories = cleanup_retention(
            repository,
            tmp_path / "audio",
            retention_days=1,
            now=time.time(),
            spool_root=spool_root,
        )
        assert directories == 1
        assert owned.exists()
        assert not orphan.exists()


def test_performance_metrics_are_bounded_and_compute_rtf() -> None:
    monitor = PerformanceMonitor(max_samples=2, max_chunks=2)
    monitor.record_capture(
        480,
        rms=0.1,
        clipping_percentage=0.0,
        queue_size=3,
        queued_seconds=0.09,
    )
    monitor.record_processed(
        2.0,
        1.0,
        1.2,
        model_id="large-v3-turbo",
        retried=True,
        cascade_retry=True,
        uncertain=True,
    )
    monitor.record_processed(4.0, 1.0, 0.8, model_id="small")
    monitor.sample_resources(queue_size=2, spool_bytes=0)
    snapshot = monitor.snapshot()
    assert snapshot["audio"]["captured_frames"] == 480
    assert snapshot["audio"]["queue_high_watermark"] == 3
    assert snapshot["current_rtf"] == 0.25
    assert snapshot["rtf_average"] == 0.375
    assert snapshot["latency_p50_s"] == 1.0
    assert snapshot["latency_p95_s"] == 1.18
    assert snapshot["current_model"] == "small"
    assert snapshot["retry_chunks"] == 1
    assert snapshot["cascade_retry_percentage"] == 50.0
    assert snapshot["uncertain_chunks"] == 1


def test_performance_metrics_can_sync_authoritative_capture_counters() -> None:
    monitor = PerformanceMonitor()
    monitor.sync_audio(
        captured_frames=16_000,
        dropped_frames=0,
        queued_audio_seconds=1.25,
        processed_audio_seconds=0.75,
        queue_high_watermark=12,
        callback_overflows=0,
        current_input_rms=0.2,
        clipping_percentage=0.5,
    )
    audio = monitor.snapshot()["audio"]
    assert audio["captured_frames"] == 16_000
    assert audio["processed_audio_seconds"] == 0.75
    assert audio["queue_high_watermark"] == 12
