from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.devices import AudioBackendUnavailable, choose_capture_rate, list_input_devices
from voxtype.audio.spool import DiskSpool, LosslessAudioQueue
from voxtype.core.models import Language, ModelProfile
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings


def test_microphone_enumeration_error_is_actionable_and_does_not_leak_backend_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSoundDevice:
        default = SimpleNamespace(device=(0, 0))

        @staticmethod
        def query_devices() -> object:
            raise RuntimeError("private backend detail")

    monkeypatch.setattr("voxtype.audio.devices._sounddevice", lambda: FailingSoundDevice())
    with pytest.raises(AudioBackendUnavailable, match="PortAudio не смог перечислить"):
        list_input_devices()


def test_unsupported_sample_rates_keep_otherwise_usable_default_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SoundDevice:
        default = SimpleNamespace(device=(0, 0))

        @staticmethod
        def query_devices() -> list[dict[str, object]]:
            return [
                {
                    "name": "USB Mic",
                    "hostapi": 0,
                    "max_input_channels": 1,
                    "default_samplerate": 44_100.0,
                }
            ]

        @staticmethod
        def query_hostapis() -> list[dict[str, str]]:
            return [{"name": "Windows WASAPI"}]

        @staticmethod
        def check_input_settings(**_settings: object) -> None:
            raise RuntimeError("rate unsupported")

    monkeypatch.setattr("voxtype.audio.devices._sounddevice", lambda: SoundDevice())
    devices = list_input_devices()
    assert len(devices) == 1
    assert devices[0].supported_sample_rates == (44_100,)
    assert choose_capture_rate(devices[0]) == 44_100


def test_capture_permission_failure_stops_dispatcher_without_leaking_thread(
    tmp_path: Path,
) -> None:
    def denied_stream(**_settings: object) -> object:
        raise PermissionError("microphone denied")

    queue = LosslessAudioQueue(max_ram_chunks=4, spool=DiskSpool(tmp_path / "spool"))
    capture = AudioCaptureService(
        queue,
        AudioCaptureConfig(input_sample_rate=16_000),
        stream_factory=denied_stream,
    )
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(PermissionError, match="microphone denied"):
        capture.start()
    after = {thread.ident for thread in threading.enumerate()}
    assert not capture.running
    assert after <= before
    assert capture.stop(drain_timeout=0.1)
    queue.close()


def test_settings_atomic_replace_failure_preserves_previous_snapshot_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    original = AppSettings()
    repository.save(original)
    previous = path.read_bytes()
    changed = AppSettings()
    changed.general.show_overlay = False

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("voxtype.settings.repository.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        repository.save(changed)
    assert path.read_bytes() == previous
    assert not tuple(tmp_path.glob(".settings.json.*.tmp"))


def test_database_lock_does_not_trigger_corruption_quarantine_or_change_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.sqlite3"
    with SessionRepository(path) as repository:
        session_id = repository.create(Language.RU, "small", ModelProfile.BALANCED, "fixture.exe")
        repository._connection.execute("PRAGMA busy_timeout=25")
        lock = sqlite3.connect(path, timeout=0.025)
        try:
            lock.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                repository.commit_text(session_id, 0, "must not appear")
        finally:
            lock.rollback()
            lock.close()
        record = repository.get(session_id)
        assert record is not None and record.committed_text == ""
        assert repository.last_recovery_backup is None


def test_corrupt_database_is_preserved_and_replaced_with_usable_empty_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.sqlite3"
    corrupt_payload = b"not a sqlite database\x00private recovery bytes"
    path.write_bytes(corrupt_payload)

    with SessionRepository(path) as repository:
        assert repository.last_recovery_backup is not None
        assert repository.last_recovery_backup.read_bytes() == corrupt_payload
        session_id = repository.create(Language.EN, "small", ModelProfile.FAST, "fixture.exe")
        assert repository.get(session_id) is not None
        assert repository.recent() == repository.unfinished()
