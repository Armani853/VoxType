from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from pytest import MonkeyPatch

from voxtype.asr.base import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from voxtype.asr.live import LiveSpeechSession
from voxtype.audio.models import AudioChunk, AudioQueueStats
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.core.controller import ApplicationController
from voxtype.core.models import Language
from voxtype.core.state_machine import AppState
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.vad.segmenter import AudioSegment, SegmentReason, StreamingSegmenter
from voxtype.windows.foreground import IntegrityLevel, WindowMetadata
from voxtype.windows.injection import InjectionStatus
from voxtype.windows.target_window import TargetDecision, TargetStatus


class AlwaysSpeech:
    def speech_probability(self, samples: object, sample_rate: int) -> float:
        del samples, sample_rate
        return 1.0

    def reset(self) -> None:
        return None


class CountingSilence:
    def __init__(self) -> None:
        self.samples_seen = 0

    def speech_probability(self, samples: object, sample_rate: int) -> float:
        del sample_rate
        self.samples_seen += int(np.asarray(samples).size)
        return 0.0

    def reset(self) -> None:
        return None


class DeterministicBackend:
    model_id = "fixture-model"

    def __init__(self) -> None:
        self.loaded = False
        self.languages: list[str] = []
        self.prompts: list[str] = []

    def load(self) -> None:
        self.loaded = True

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.languages.append(request.language)
        self.prompts.append(request.initial_prompt)
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


class FailingBackend(DeterministicBackend):
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        raise RuntimeError("fixture inference failed")


class HangingBackend(DeterministicBackend):
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        time.sleep(0.5)
        return super().transcribe(request)


class DelayedLoadBackend(DeterministicBackend):
    def __init__(self) -> None:
        super().__init__()
        self.load_calls = 0

    def load(self) -> None:
        self.load_calls += 1
        time.sleep(0.1)
        super().load()


class LanguageBackend(DeterministicBackend):
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.languages.append(request.language)
        self.prompts.append(request.initial_prompt)
        text = "русский" if request.language == "ru" else "english"
        word = WordTimestamp(
            text, request.start_offset_seconds, request.start_offset_seconds + 0.3, 0.99
        )
        segment = TranscriptionSegment(
            text,
            request.start_offset_seconds,
            request.start_offset_seconds + 0.3,
            (word,),
            average_log_probability=-0.01,
            no_speech_probability=0.01,
            compression_ratio=1.0,
        )
        return TranscriptionResult(
            text,
            request.language,
            (segment,),
            request.duration_seconds,
            0.01,
            self.model_id,
            "fixture",
        )


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

    def feed(self, sequence: int) -> None:
        samples = np.full(16_000, 0.2, dtype=np.float32)
        self.output_queue.put(AudioChunk(sequence, samples, 16_000, time.time()))
        self.output_queue.record_capture(samples.size)


class LongSilenceCapture(FixtureCapture):
    chunk_count = 25
    chunk_samples = 800

    def start(self) -> None:
        self.running = True
        samples = np.zeros(self.chunk_samples, dtype=np.float32)
        for sequence in range(self.chunk_count):
            self.output_queue.put(AudioChunk(sequence, samples, 16_000, time.time()))
            self.output_queue.record_capture(samples.size)


class EmptyCapture(FixtureCapture):
    def start(self) -> None:
        self.running = True


class BlockingStartCapture(EmptyCapture):
    def __init__(
        self,
        output_queue: LosslessAudioQueue,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(output_queue)
        self.entered = entered
        self.release = release

    def start(self) -> None:
        self.entered.set()
        if not self.release.wait(2.0):
            raise TimeoutError("test capture start was not released")
        super().start()


class NonStoppingCapture(FixtureCapture):
    def stop(self, *, drain_timeout: float = 5.0) -> bool:
        del drain_timeout
        return False


class SlowStopCapture(EmptyCapture):
    def stop(self, *, drain_timeout: float = 5.0) -> bool:
        del drain_timeout
        time.sleep(0.2)
        self.running = False
        return True


class FixtureTargetManager:
    def __init__(self) -> None:
        self.end_calls = 0
        self.target = WindowMetadata(
            101, "Fixture editor", 202, "fixture.exe", IntegrityLevel.MEDIUM
        )

    @property
    def locked_target(self) -> WindowMetadata:
        return self.target

    def start_session(self) -> TargetDecision:
        return TargetDecision(TargetStatus.READY, self.target, self.target, "ready")

    def resolve(self) -> TargetDecision:
        return TargetDecision(TargetStatus.READY, self.target, self.target, "ready")

    def end_session(self) -> None:
        self.end_calls += 1


class FixtureInjection:
    def __init__(self) -> None:
        self.fragments: list[str] = []

    def submit(self, text: str) -> object:
        self.fragments.append(text)
        return SimpleNamespace(status=InjectionStatus.INSERTED)

    def close(self) -> None:
        return None


class QueuedInjection(FixtureInjection):
    def __init__(self) -> None:
        super().__init__()
        self.pending_fragments = 0

    def submit(self, text: str) -> object:
        self.fragments.append(text)
        self.pending_fragments += 1
        return SimpleNamespace(
            status=InjectionStatus.QUEUED,
            target_status=TargetStatus.TARGET_INACTIVE,
        )

    def flush(self) -> object:
        self.pending_fragments = 0
        return SimpleNamespace(status=InjectionStatus.INSERTED)


def wait_for_state(
    controller: ApplicationController, expected: AppState, timeout_s: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout_s
    while controller.state is not expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller.state is expected


def _settings_with_recovery(tmp_path: Path) -> SettingsRepository:
    repository = SettingsRepository(tmp_path / "settings.json")
    # This suite exercises durable history and PCM recovery with explicit consent.
    repository.update({"privacy": {"save_history": True, "save_audio": True}})
    return repository


def test_prompt_context_resets_for_sentence_pause_uncertainty_and_language(
    tmp_path: Path,
) -> None:
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=SessionRepository(tmp_path / "sessions.sqlite3"),
        backend=DeterministicBackend(),
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )

    def segment(sequence: int, start_s: float, end_s: float) -> AudioSegment:
        start = round(start_s * 16_000)
        end = round(end_s * 16_000)
        return AudioSegment(
            sequence,
            np.zeros(end - start, dtype=np.float32),
            16_000,
            start,
            end,
            SegmentReason.FLUSH,
            0.0,
            end_s - start_s,
        )

    assert controller._safe_prompt_context(segment(0, 0.0, 1.0), Language.RU, "не закончено")
    assert controller._safe_prompt_context(segment(1, 1.1, 2.0), Language.RU, "Закончено.") == ""
    controller._last_prompt_result_uncertain = True
    assert controller._safe_prompt_context(segment(2, 2.1, 3.0), Language.RU, "сомнительно") == ""
    controller._last_prompt_result_uncertain = False
    assert controller._safe_prompt_context(segment(3, 3.1, 4.0), Language.EN, "English") == ""
    assert controller._safe_prompt_context(segment(4, 13.0, 14.0), Language.EN, "long gap") == ""
    controller.session_repository.close()


def test_controller_journals_before_ordered_injection_and_drains(tmp_path: Path) -> None:
    settings_repository = _settings_with_recovery(tmp_path)
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
    wait_for_state(controller, AppState.COMPLETED)
    assert controller.snapshot().queued_audio_seconds == 0.0

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


def test_inactive_target_completion_is_not_history_error_and_can_retry(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    target = FixtureTargetManager()
    injection = QueuedInjection()
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
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
    wait_for_state(controller, AppState.COMPLETED)
    assert repository.recent(1)[0].status == "completed"
    assert injection.pending_fragments == 1
    assert target.end_calls == 0
    assert controller.retry_completed_insertion()
    assert target.end_calls == 1
    assert not controller.retry_completed_insertion()
    assert target.end_calls == 1
    assert controller.shutdown()
    repository.close()


def test_model_loading_accepts_only_one_queued_start(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    backend = DelayedLoadBackend()
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=backend,
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    assert controller.state in {AppState.STARTING, AppState.MODEL_LOADING}
    assert not controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert backend.load_calls == 1
    assert len(repository.recent()) == 1
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    controller.shutdown()
    repository.close()


def test_shutdown_waits_for_model_loader_before_disposing_resources(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    backend = DelayedLoadBackend()
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=backend,
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.MODEL_LOADING)
    assert controller.shutdown()
    assert backend.load_calls == 1
    assert controller._start_thread is not None
    assert not controller._start_thread.is_alive()
    repository.close()


def test_capture_creation_error_is_recoverable_from_starting(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")

    def disconnected_capture(_queue: LosslessAudioQueue) -> FixtureCapture:
        raise OSError("audio device disconnected")

    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=disconnected_capture,  # type: ignore[arg-type]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.ERROR_RECOVERABLE)
    assert controller.shutdown()
    repository.close()


def test_input_overflow_warns_and_continues_capture(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    captures: list[FixtureCapture] = []

    def make_capture(queue: LosslessAudioQueue) -> FixtureCapture:
        capture = FixtureCapture(queue)
        captures.append(capture)
        return capture

    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=make_capture,  # type: ignore[arg-type]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    captures[0].output_queue.put(
        AudioChunk(1, np.zeros(480, dtype=np.float32), 16_000, time.time(), True)
    )
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        sessions = repository.recent(1)
        if sessions and "AudioInputOverflow" in sessions[0].warnings:
            break
        time.sleep(0.01)
    assert sessions and "AudioInputOverflow" in sessions[0].warnings
    assert controller.state is AppState.LISTENING
    assert "Микрофон потерял часть звука" in controller.snapshot().status_message
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    assert controller.shutdown()
    repository.close()


def test_shutdown_waits_for_inflight_inference(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=HangingBackend(),
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.shutdown()
    assert controller._pipeline_thread is None or not controller._pipeline_thread.is_alive()
    repository.close()


def test_start_command_returns_before_slow_runtime_preparation(tmp_path: Path) -> None:
    def slow_segmenter() -> StreamingSegmenter:
        time.sleep(0.2)
        return StreamingSegmenter(AlwaysSpeech())

    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=lambda queue: EmptyCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=slow_segmenter,
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    started = time.monotonic()
    assert controller.start_dictation()
    assert time.monotonic() - started < 0.05
    assert controller.state is AppState.STARTING
    wait_for_state(controller, AppState.LISTENING)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    assert len(repository.recent()) == 1
    controller.shutdown()
    repository.close()


def test_snapshot_remains_responsive_while_audio_driver_start_blocks(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=lambda queue: BlockingStartCapture(queue, entered, release),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    assert entered.wait(2.0)
    timer = threading.Timer(0.35, release.set)
    timer.start()
    try:
        started = time.monotonic()
        controller.snapshot()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        timer.join()
        controller.shutdown()
        repository.close()
    assert elapsed < 0.15


def test_stop_during_blocked_audio_start_closes_capture_without_listening(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    captures: list[BlockingStartCapture] = []
    repository = SessionRepository(tmp_path / "sessions.sqlite3")

    def capture_factory(queue: LosslessAudioQueue) -> BlockingStartCapture:
        capture = BlockingStartCapture(queue, entered, release)
        captures.append(capture)
        return capture

    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=capture_factory,  # type: ignore[arg-type]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    assert entered.wait(2.0)
    assert controller.stop_dictation()
    release.set()
    wait_for_state(controller, AppState.COMPLETED)
    assert captures and not captures[0].running
    assert controller.shutdown()
    repository.close()


def test_snapshot_remains_responsive_while_audio_resume_blocks(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    capture_count = 0

    def capture_factory(queue: LosslessAudioQueue) -> FixtureCapture:
        nonlocal capture_count
        capture_count += 1
        if capture_count == 1:
            return EmptyCapture(queue)
        return BlockingStartCapture(queue, entered, release)

    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=capture_factory,  # type: ignore[arg-type]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.toggle_pause()
    wait_for_state(controller, AppState.PAUSED)
    assert controller.toggle_pause()
    assert entered.wait(2.0)
    timer = threading.Timer(0.35, release.set)
    timer.start()
    try:
        started = time.monotonic()
        controller.snapshot()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        timer.join()
        controller.shutdown()
        repository.close()
    assert elapsed < 0.15


def test_stop_during_model_loading_is_immediate_and_finishes_once(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DelayedLoadBackend(),
        capture_factory=lambda queue: EmptyCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    started = time.monotonic()
    assert controller.stop_dictation()
    assert time.monotonic() - started < 0.05
    assert controller.state is AppState.FINISHING
    assert not controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    assert len(repository.recent()) == 1
    controller.shutdown()
    repository.close()


def test_pause_resume_captures_no_text_while_paused(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    captures: list[EmptyCapture] = []

    def capture_factory(queue: LosslessAudioQueue) -> EmptyCapture:
        capture = EmptyCapture(queue)
        captures.append(capture)
        return capture

    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=capture_factory,  # type: ignore[arg-type]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.toggle_pause()
    wait_for_state(controller, AppState.PAUSED)
    captures[0].feed(99)
    time.sleep(0.05)
    assert repository.recent()[0].committed_text == ""
    assert controller.toggle_pause()
    wait_for_state(controller, AppState.LISTENING)
    assert len(captures) == 2
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    assert repository.recent()[0].committed_text == ""
    controller.shutdown()
    repository.close()


def test_stop_freezes_timer_before_slow_capture_drain(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=DeterministicBackend(),
        capture_factory=lambda queue: SlowStopCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    time.sleep(0.03)
    assert controller.stop_dictation()
    stopped_elapsed = controller.snapshot().session_elapsed_s
    time.sleep(0.08)
    assert controller.state is AppState.FINISHING
    assert controller.snapshot().session_elapsed_s == stopped_elapsed
    assert not controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    assert repository.recent()[0].committed_text == ""
    controller.shutdown()
    repository.close()


def test_controller_never_finalizes_completed_when_audio_dispatcher_does_not_stop(
    tmp_path: Path,
) -> None:
    settings_repository = _settings_with_recovery(tmp_path)
    session_repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=DeterministicBackend(),
        capture_factory=lambda queue: NonStoppingCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.ERROR_RECOVERABLE)
    records = session_repository.recent()
    assert records[0].status == "recovery_required"
    session_repository.close()


def test_inference_watchdog_allows_retry_once_native_call_exits(tmp_path: Path) -> None:
    settings_repository = _settings_with_recovery(tmp_path)
    session_repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=HangingBackend(),
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
        inference_timeout_s=0.05,
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.ERROR_RECOVERABLE)
    assert not controller.start_dictation()
    worker = controller._inference_thread
    if worker is not None:
        worker.join(timeout=2)
    assert controller.shutdown()
    assert session_repository.recent()[0].status == "recovery_required"
    session_repository.close()


def test_live_preview_keeps_vad_draining_and_yields_to_final(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    preview_started = threading.Event()
    preview_release = threading.Event()
    second_block_analyzed = threading.Event()
    calls: list[str] = []

    class ImmediateLiveSession(LiveSpeechSession):
        def request(self, speech_samples: int, now: float) -> TranscriptionRequest | None:
            if not preview_started.is_set():
                self._next = 0
            return super().request(speech_samples, now)

    class BlockingPreviewBackend(DeterministicBackend):
        def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
            kind = request.metadata["kind"]
            calls.append(kind)
            if kind == "preview":
                preview_started.set()
                if not preview_release.wait(5):
                    raise TimeoutError("test did not release preview")
            return super().transcribe(request)

    class ObservedSpeech(AlwaysSpeech):
        samples_seen = 0

        def speech_probability(self, samples: object, sample_rate: int) -> float:
            self.samples_seen += np.asarray(samples).size
            if self.samples_seen > 16_000:
                second_block_analyzed.set()
            return super().speech_probability(samples, sample_rate)

    monkeypatch.setattr("voxtype.core.controller.LiveSpeechSession", ImmediateLiveSession)
    settings = _settings_with_recovery(tmp_path)
    settings.update({"dictation": {"live_preview": True}})
    injection = FixtureInjection()
    controller = ApplicationController(
        settings_repository=settings,
        session_repository=SessionRepository(tmp_path / "sessions.sqlite3"),
        backend=BlockingPreviewBackend(),
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(ObservedSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=injection,  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    try:
        controller.initialize()
        assert controller.start_dictation()
        assert preview_started.wait(5)
        capture = controller._capture
        assert isinstance(capture, FixtureCapture)
        capture.feed(1)
        assert second_block_analyzed.wait(2)
        assert controller.stop_dictation()
        assert calls == ["preview"]
        assert injection.fragments == []
        preview_release.set()
        wait_for_state(controller, AppState.COMPLETED)
        assert calls == ["preview", "final"]
        assert controller.snapshot().live_transcript is None
        assert injection.fragments == ["Привет мир"]
    finally:
        preview_release.set()
        controller.shutdown()


def test_cancel_processing_drops_late_result_and_next_dictation_works(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowFirstCall(DeterministicBackend):
        def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release native inference")
            return super().transcribe(request)

    repository = SessionRepository(tmp_path / "sessions.sqlite3")
    injection = FixtureInjection()
    controller = ApplicationController(
        settings_repository=_settings_with_recovery(tmp_path),
        session_repository=repository,
        backend=SlowFirstCall(),
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=injection,  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    try:
        controller.initialize()
        assert controller.start_dictation()
        wait_for_state(controller, AppState.LISTENING)
        assert controller.stop_dictation()
        assert entered.wait(5)
        assert controller.cancel_processing()
        wait_for_state(controller, AppState.ERROR_RECOVERABLE)
        assert not controller.start_dictation()
        native = controller._inference_thread
        finisher = controller._finish_thread
        release.set()
        for worker in (native, finisher):
            if worker is not None:
                worker.join(timeout=5)
                assert not worker.is_alive()
        assert not injection.fragments
        assert repository.recent()[0].status == "recovery_required"
        assert controller.restart_dictation()
        wait_for_state(controller, AppState.LISTENING)
        assert controller.stop_dictation()
        wait_for_state(controller, AppState.COMPLETED)
        assert injection.fragments == ["Привет мир"]
    finally:
        release.set()
        controller.shutdown()
        repository.close()


def test_long_silence_does_not_exhaust_durable_inflight_leases(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    settings_repository = _settings_with_recovery(tmp_path)
    session_repository = SessionRepository(tmp_path / "sessions.sqlite3")
    detector = CountingSilence()

    def small_inflight_queue(**kwargs: object) -> LosslessAudioQueue:
        # Larger than the segmenter's 612 ms pre-roll window, but much smaller
        # than the silent source, so a failure to acknowledge old silence stalls.
        kwargs["max_inflight"] = 16
        return LosslessAudioQueue(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("voxtype.core.controller.LosslessAudioQueue", small_inflight_queue)
    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=DeterministicBackend(),
        capture_factory=lambda queue: LongSilenceCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(detector),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING, timeout_s=15.0)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED, timeout_s=15.0)

    expected_samples = LongSilenceCapture.chunk_count * LongSilenceCapture.chunk_samples
    assert detector.samples_seen == expected_samples
    snapshot = controller.snapshot()
    assert snapshot.delivery_status == "no_result"
    assert "Речь не распознана" in snapshot.status_message
    assert session_repository.recent()[0].dropped_frames == 0
    assert not any((tmp_path / "spool").rglob("*.vtxpcm"))
    controller.shutdown()
    session_repository.close()


def test_runtime_error_preserves_pcm_then_recovers_tail_before_retry(tmp_path: Path) -> None:
    settings_repository = _settings_with_recovery(tmp_path)
    session_repository = SessionRepository(tmp_path / "sessions.sqlite3")
    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=FailingBackend(),
        capture_factory=lambda queue: FixtureCapture(queue),  # type: ignore[arg-type,return-value]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.ERROR_RECOVERABLE)

    records = session_repository.recent()
    assert len(records) == 1
    assert records[0].finished_at is None
    assert records[0].status == "recovery_required"
    assert any((tmp_path / "spool" / records[0].session_id).glob("*.vtxpcm"))
    assert controller.configure_backend(
        DeterministicBackend(), controller.settings.dictation.model_profile
    )
    recovered = controller.recover_session(records[0].session_id)
    assert recovered == "Привет мир"
    restored = session_repository.get(records[0].session_id)
    assert restored is not None
    assert restored.finished_at is not None
    assert restored.status == "recovered"
    assert not (tmp_path / "spool" / records[0].session_id).exists()
    assert controller.start_dictation()
    controller.shutdown()
    session_repository.close()


def test_language_switch_applies_on_next_session(
    tmp_path: Path,
) -> None:
    settings_repository = _settings_with_recovery(tmp_path)
    session_repository = SessionRepository(tmp_path / "sessions.sqlite3")
    backend = LanguageBackend()
    captures: list[FixtureCapture] = []

    def capture_factory(queue: LosslessAudioQueue) -> FixtureCapture:
        capture = FixtureCapture(queue)
        captures.append(capture)
        return capture

    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=backend,
        capture_factory=capture_factory,  # type: ignore[arg-type]
        segmenter_factory=lambda: StreamingSegmenter(AlwaysSpeech()),
        target_manager=FixtureTargetManager(),  # type: ignore[arg-type]
        injection_service=FixtureInjection(),  # type: ignore[arg-type]
        spool_root=tmp_path / "spool",
    )
    controller.initialize()
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    time.sleep(0.05)
    controller.switch_language()
    captures[0].feed(1)
    time.sleep(0.05)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)

    record = session_repository.recent()[0]
    assert "Русский" in record.committed_text
    assert backend.languages == ["ru"]
    assert controller.start_dictation()
    wait_for_state(controller, AppState.LISTENING)
    assert controller.stop_dictation()
    wait_for_state(controller, AppState.COMPLETED)
    assert "english" in session_repository.recent()[0].committed_text.casefold()
    assert backend.languages == ["ru", "en"]
    assert backend.prompts == ["", ""]
    controller.shutdown()
    session_repository.close()
