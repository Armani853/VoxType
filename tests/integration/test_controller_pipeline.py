from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from voxtype.asr.base import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from voxtype.audio.models import AudioChunk, AudioQueueStats
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.core.controller import ApplicationController
from voxtype.core.state_machine import AppState
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.vad.segmenter import StreamingSegmenter
from voxtype.windows.foreground import IntegrityLevel, WindowMetadata
from voxtype.windows.injection import InjectionStatus
from voxtype.windows.target_window import TargetDecision, TargetStatus


class AlwaysSpeech:
    def speech_probability(self, samples: object, sample_rate: int) -> float:
        del samples, sample_rate
        return 1.0

    def reset(self) -> None:
        return None


class DeterministicBackend:
    model_id = "fixture-model"

    def __init__(self) -> None:
        self.loaded = False
        self.languages: list[str] = []

    def load(self) -> None:
        self.loaded = True

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.languages.append(request.language)
        words = (
            WordTimestamp(
                "привет", request.start_offset_seconds, request.start_offset_seconds + 0.3, 0.99
            ),
            WordTimestamp(
                "мир", request.start_offset_seconds + 0.3, request.start_offset_seconds + 0.6, 0.99
            ),
        )
        segment = TranscriptionSegment(
            "привет мир",
            request.start_offset_seconds,
            request.start_offset_seconds + 0.6,
            words,
            average_log_probability=-0.01,
            no_speech_probability=0.01,
            compression_ratio=1.0,
        )
        return TranscriptionResult(
            text="привет мир",
            language=request.language,
            segments=(segment,),
            audio_duration_seconds=request.duration_seconds,
            processing_seconds=0.01,
            model_id=self.model_id,
            backend_name="fixture",
        )

    def unload(self) -> None:
        self.loaded = False


class FixtureCapture:
    def __init__(self, output_queue: LosslessAudioQueue) -> None:
        self.output_queue = output_queue
        self.running = False

    def start(self) -> None:
        self.running = True
        samples = np.full(16_000, 0.2, dtype=np.float32)
        self.output_queue.put(AudioChunk(0, samples, 16_000, time.time()))
        self.output_queue.record_capture(samples.size)

    def stop(self, *, drain_timeout: float = 5.0) -> bool:
        del drain_timeout
        self.running = False
        return True

    def stats(self) -> AudioQueueStats:
        return self.output_queue.snapshot()


class FixtureTargetManager:
    def __init__(self) -> None:
        self.target = WindowMetadata(
            101, "Fixture editor", 202, "fixture.exe", IntegrityLevel.MEDIUM
        )

    @property
    def locked_target(self) -> WindowMetadata:
        return self.target

    def start_session(self) -> TargetDecision:
        return TargetDecision(TargetStatus.READY, self.target, self.target, "ready")

    def end_session(self) -> None:
        return None


class FixtureInjection:
    def __init__(self) -> None:
        self.fragments: list[str] = []

    def submit(self, text: str) -> object:
        self.fragments.append(text)
        return SimpleNamespace(status=InjectionStatus.INSERTED)

    def close(self) -> None:
        return None


def wait_for_state(
    controller: ApplicationController, expected: AppState, timeout_s: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout_s
    while controller.state is not expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller.state is expected


def test_controller_journals_before_ordered_injection_and_drains(tmp_path: Path) -> None:
    settings_repository = SettingsRepository(tmp_path / "settings.json")
    session_repository = SessionRepository(tmp_path / "sessions.sqlite3")
    backend = DeterministicBackend()
    target = FixtureTargetManager()
    injection = FixtureInjection()
    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=backend,
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=target,  # type: ignore[arg-type]
        injection_service=injection,  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.IDLE)

    records = session_repository.recent()
    assert len(records) == 1
    assert records[0].committed_text == "Привет мир"
    assert records[0].status == "completed"
    assert records[0].dropped_frames == 0
    assert injection.fragments == ["Привет мир"]
    assert backend.languages == ["ru"]
    assert not any((tmp_path / "spool").rglob("*.vtxpcm"))
    controller.shutdown()
    session_repository.close()
