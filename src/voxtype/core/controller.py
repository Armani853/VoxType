"""Lifecycle controller joining capture, segmentation, ASR, journal and injection."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from voxtype.asr.base import ASRBackend, TranscriptionRequest, bounded_prompt
from voxtype.asr.hallucination_guard import HallucinationGuard
from voxtype.audio.capture import AudioCaptureService
from voxtype.audio.models import AudioQueueStats
from voxtype.audio.spool import AudioQueueLease, DiskSpool, LosslessAudioQueue
from voxtype.core.models import (
    ControllerSnapshot,
    Language,
    ModelProfile,
    TranscriptFragment,
    WordStamp,
)
from voxtype.core.state_machine import AppState, StateMachine
from voxtype.monitoring.metrics import PerformanceMonitor
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings
from voxtype.transcript.commands import VoiceCommandProcessor
from voxtype.transcript.merger import TranscriptMerger
from voxtype.transcript.vocabulary import Vocabulary
from voxtype.vad.segmenter import AudioSegment, StreamingSegmenter
from voxtype.windows.injection import InjectionStatus, TextInjectionService
from voxtype.windows.target_window import TargetWindowManager

CaptureFactory = Callable[[LosslessAudioQueue], AudioCaptureService]
SegmenterFactory = Callable[[], StreamingSegmenter]


class ApplicationController:
    """Thread-safe command surface consumed by hotkeys, tray and Qt widgets.

    One pipeline thread owns the segmenter and backend calls. Audio can therefore
    spill to disk under ASR backlog without reordering committed text.
    """

    def __init__(
        self,
        *,
        settings_repository: SettingsRepository,
        session_repository: SessionRepository,
        backend: ASRBackend,
        capture_factory: CaptureFactory,
        segmenter_factory: SegmenterFactory,
        target_manager: TargetWindowManager,
        injection_service: TextInjectionService,
        spool_root: Path,
        microphone_name: str = "Системный микрофон",
        vocabulary: Vocabulary | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings_repository = settings_repository
        self.session_repository = session_repository
        self.backend = backend
        self.capture_factory = capture_factory
        self.segmenter_factory = segmenter_factory
        self.target_manager = target_manager
        self.injection_service = injection_service
        self.spool_root = spool_root.resolve()
        self.spool_root.mkdir(parents=True, exist_ok=True)
        self.microphone_name = microphone_name
        self.vocabulary = vocabulary or Vocabulary()
        self.logger = logger or logging.getLogger("voxtype.controller")
        self.state_machine = StateMachine(logger=self.logger)
        self.metrics = PerformanceMonitor()
        self.guard = HallucinationGuard()
        self.merger = TranscriptMerger()
        self.settings: AppSettings = self.settings_repository.load()
        self._language = self.settings.dictation.language
        self._lock = threading.RLock()
        self._capture: AudioCaptureService | None = None
        self._audio_queue: LosslessAudioQueue | None = None
        self._spool: DiskSpool | None = None
        self._segmenter: StreamingSegmenter | None = None
        self._journal: SessionJournal | None = None
        self._session_id: str | None = None
        self._session_started_monotonic: float | None = None
        self._pipeline_thread: threading.Thread | None = None
        self._finish_thread: threading.Thread | None = None
        self._pending_delivery_thread: threading.Thread | None = None
        self._language_switches: deque[tuple[float, Language]] = deque()
        self._worker_language = self._language
        self._user_paused = False
        self._last_fragment = ""
        self._provisional = ""
        self._status_message = ""
        self._error_message = ""
        self._last_stats: AudioQueueStats | None = None
        self._shutdown_requested = threading.Event()

    @property
    def state(self) -> AppState:
        return self.state_machine.state

    @property
    def language(self) -> Language:
        with self._lock:
            return self._language

    def initialize(self, *, preload_model: bool = False) -> None:
        if self.state is not AppState.STARTING:
            return
        if preload_model:
            self.state_machine.transition(AppState.MODEL_LOADING, "preload installed model")
            thread = threading.Thread(
                target=self._preload_model, name="voxtype-model-loader", daemon=True
            )
            thread.start()
        else:
            self.state_machine.transition(AppState.IDLE, "application initialized")

    def _preload_model(self) -> None:
        try:
            self.backend.load()
        except Exception as exc:
            self.logger.warning("model_preload_failed type=%s", type(exc).__name__)
            with self._lock:
                self._status_message = "Модель не установлена или не загрузилась"
            if self.state is AppState.MODEL_LOADING:
                self.state_machine.transition(AppState.ERROR_RECOVERABLE, "model preload failed")
        else:
            if self.state is AppState.MODEL_LOADING:
                self.state_machine.transition(AppState.IDLE, "model preloaded")

    def toggle_dictation(self) -> None:
        current = self.state
        if current in {AppState.IDLE, AppState.ERROR_RECOVERABLE}:
            self.start_dictation()
        elif current in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
            AppState.PAUSED_TARGET_LOST,
        }:
            self.stop_dictation()

    def start_dictation(self) -> bool:
        with self._lock:
            if self.state not in {AppState.IDLE, AppState.ERROR_RECOVERABLE}:
                return False
            decision = self.target_manager.start_session()
            target_name = decision.target.process_name if decision.target is not None else ""
            profile = self.settings.dictation.model_profile
            self._session_id = self.session_repository.create(
                self._language,
                self.backend.model_id,
                profile,
                target_name,
            )
            self._journal = SessionJournal(self.session_repository, self._session_id)
            self._journal.append(
                "session_started",
                {
                    "target_status": decision.status.value,
                    "language": self._language.value,
                    "model": self.backend.model_id,
                },
            )
            session_spool = self.spool_root / self._session_id
            self._spool = DiskSpool(session_spool, max_bytes=4 * 1024**3)
            self._audio_queue = LosslessAudioQueue(
                max_ram_chunks=128,
                spool=self._spool,
                spill_threshold=0.70,
                max_inflight=1,
            )
            self._segmenter = self.segmenter_factory()
            self._capture = self.capture_factory(self._audio_queue)
            self._worker_language = self._language
            self._language_switches.clear()
            self._session_started_monotonic = time.monotonic()
            self._last_fragment = ""
            self._provisional = ""
            self._error_message = ""
            self._status_message = "Подготовка модели"
            self._user_paused = False
            self.merger.reset()
            if self.state is AppState.ERROR_RECOVERABLE:
                self.state_machine.transition(AppState.IDLE, "retry requested")
            self.state_machine.transition(AppState.MODEL_LOADING, "starting dictation")
        starter = threading.Thread(
            target=self._load_and_start_runtime,
            name="voxtype-session-starter",
            daemon=True,
        )
        starter.start()
        return True

    def _load_and_start_runtime(self) -> None:
        try:
            self.backend.load()
            with self._lock:
                audio_queue = self._audio_queue
                capture = self._capture
                if audio_queue is None or capture is None:
                    raise RuntimeError("session runtime was disposed while loading")
                self._pipeline_thread = threading.Thread(
                    target=self._pipeline_loop,
                    name="voxtype-transcription",
                    daemon=True,
                )
                self._pipeline_thread.start()
                capture.start()
                self._status_message = "Слушаю"
            if self.state is AppState.MODEL_LOADING:
                self.state_machine.transition(AppState.IDLE, "model ready for session")
            if self.state is AppState.IDLE:
                self.state_machine.transition(AppState.LISTENING, "microphone capture started")
        except Exception as exc:
            self._handle_runtime_error("Не удалось начать диктовку", exc)

    def stop_dictation(self) -> bool:
        with self._lock:
            if self.state not in {
                AppState.LISTENING,
                AppState.SPEECH_DETECTED,
                AppState.TRANSCRIBING,
                AppState.PAUSED_TARGET_LOST,
            }:
                return False
            self.state_machine.transition(AppState.FINISHING, "user stopped dictation")
            self._status_message = "Обрабатываю оставшийся звук"
            capture = self._capture
            audio_queue = self._audio_queue
        if capture is not None:
            capture.stop(drain_timeout=10.0)
            with self._lock:
                self._last_stats = capture.stats()
        if audio_queue is not None:
            audio_queue.close()
        finisher = threading.Thread(
            target=self._wait_for_pipeline, name="voxtype-session-finisher", daemon=True
        )
        with self._lock:
            self._finish_thread = finisher
        finisher.start()
        return True

    def _wait_for_pipeline(self) -> None:
        with self._lock:
            pipeline = self._pipeline_thread
        if pipeline is not None and pipeline is not threading.current_thread():
            while pipeline.is_alive():
                pipeline.join(timeout=0.25)
                if self._shutdown_requested.is_set() and pipeline.is_alive():
                    self._status_message = "Ожидаю завершения локального inference"
        self._complete_session("completed" if not self._error_message else "error")

    def _pipeline_loop(self) -> None:
        with self._lock:
            audio_queue = self._audio_queue
            segmenter = self._segmenter
        if audio_queue is None or segmenter is None:
            self._handle_runtime_error("Внутренняя аудиоочередь не создана", RuntimeError())
            return
        failed_lease: AudioQueueLease | None = None
        try:
            while True:
                lease = audio_queue.acquire(timeout=0.1)
                if lease is None:
                    if audio_queue.closed:
                        break
                    continue
                failed_lease = lease
                self._apply_language_switches(lease.chunk.captured_at, segmenter)
                segments = segmenter.process(lease.chunk.samples)
                for segment in segments:
                    self._transcribe_segment(segment, self._worker_language)
                audio_queue.ack(lease)
                failed_lease = None
            for segment in segmenter.flush():
                self._transcribe_segment(segment, self._worker_language)
        except Exception as exc:
            if failed_lease is not None:
                try:
                    audio_queue.nack(failed_lease)
                except (ValueError, OSError):
                    self.logger.exception("audio_lease_nack_failed")
            self._handle_runtime_error("Ошибка локального распознавания", exc)

    def _apply_language_switches(self, captured_at: float, segmenter: StreamingSegmenter) -> None:
        while True:
            with self._lock:
                if not self._language_switches or captured_at < self._language_switches[0][0]:
                    return
                _switch_time, language = self._language_switches.popleft()
            for segment in segmenter.flush():
                self._transcribe_segment(segment, self._worker_language)
            segmenter.reset()
            self._worker_language = language

    def _transcribe_segment(self, segment: AudioSegment, language: Language) -> None:
        current = self.state
        if current not in {AppState.FINISHING, AppState.PAUSED_TARGET_LOST}:
            if current is AppState.LISTENING:
                self.state_machine.transition(AppState.SPEECH_DETECTED, "VAD emitted speech")
            if self.state is AppState.SPEECH_DETECTED:
                self.state_machine.transition(AppState.TRANSCRIBING, "ASR inference started")
        committed = self.merger.committed_text
        request = TranscriptionRequest(
            audio=segment.samples,
            sample_rate=segment.sample_rate,
            language=language.value,
            start_offset_seconds=segment.start_seconds,
            initial_prompt=bounded_prompt(committed, self.vocabulary.entries),
            condition_on_previous_text=False,
            metadata={"segment_sequence": str(segment.sequence)},
        )
        with self._lock:
            self._provisional = "Распознаю…"
        guarded = self.guard.transcribe_guarded(self.backend, request, committed_text=committed)
        result = guarded.result
        text = VoiceCommandProcessor(self.settings.dictation.voice_commands).process(
            result.text, language
        )
        log_probabilities = [
            item.average_log_probability
            for item in result.segments
            if item.average_log_probability is not None
        ]
        ratios = [
            item.compression_ratio for item in result.segments if item.compression_ratio is not None
        ]
        no_speech = [
            item.no_speech_probability
            for item in result.segments
            if item.no_speech_probability is not None
        ]
        fragment = TranscriptFragment(
            sequence=segment.sequence,
            text=text,
            words=tuple(
                WordStamp(word.word, word.start, word.end, word.probability or 0.0)
                for word in result.words
            ),
            chunk_start_s=segment.start_seconds,
            chunk_end_s=segment.end_seconds,
            language=language,
            average_log_probability=sum(log_probabilities) / len(log_probabilities)
            if log_probabilities
            else 0.0,
            compression_ratio=max(ratios, default=1.0),
            no_speech_probability=max(no_speech, default=0.0),
            inference_s=result.processing_seconds,
        )
        outcome = self.merger.merge(fragment)
        self.metrics.record_processed(
            result.audio_duration_seconds,
            result.processing_seconds,
            result.processing_seconds,
        )
        journal = self._journal
        if guarded.assessment.suspicious and journal is not None:
            warning = ",".join(issue.value for issue in guarded.assessment.issues)
            self.session_repository.add_warning(
                self._require_session_id(), warning, suspicious=True
            )
        if outcome.insertion_text:
            if journal is None:
                raise RuntimeError("committed text has no active journal")
            journal.commit_text(outcome.insertion_text)
            injection = self.injection_service.submit(outcome.insertion_text)
            with self._lock:
                self._last_fragment = outcome.insertion_text.strip()
                self._provisional = ""
                if injection.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                    self._status_message = "Целевое окно неактивно; текст сохранён"
                elif injection.status is InjectionStatus.FAILED:
                    self._status_message = "Вставка не удалась; текст сохранён"
            if injection.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                if self.state in {
                    AppState.LISTENING,
                    AppState.SPEECH_DETECTED,
                    AppState.TRANSCRIBING,
                }:
                    self.state_machine.transition(
                        AppState.PAUSED_TARGET_LOST, "target unavailable for injection"
                    )
            elif self.state is AppState.PAUSED_TARGET_LOST and not self._user_paused:
                self.state_machine.transition(AppState.LISTENING, "target became available")
        else:
            with self._lock:
                self._provisional = ""
        if self.state in {AppState.TRANSCRIBING, AppState.SPEECH_DETECTED}:
            self.state_machine.transition(AppState.LISTENING, "segment committed")

    def switch_language(self) -> Language:
        switch_time = time.time()
        with self._lock:
            new_language = self._language.toggled()
            self._language = new_language
            self.settings.dictation.language = new_language
            self.settings_repository.save(self.settings)
            if self._session_id is not None:
                self._language_switches.append((switch_time, new_language))
                if self._journal is not None:
                    self._journal.append(
                        "language_switch",
                        {"language": new_language.value, "captured_at": switch_time},
                    )
            return new_language

    def configure_backend(self, backend: ASRBackend, profile: ModelProfile) -> bool:
        """Switch an installed model only while no session owns the current one."""
        with self._lock:
            if self._session_id is not None or self.state not in {
                AppState.IDLE,
                AppState.ERROR_RECOVERABLE,
            }:
                return False
            previous = self.backend
            self.backend = backend
            self.settings.dictation.model_profile = profile
            self.settings.model.installed_model = backend.model_id
            self.settings_repository.save(self.settings)
            self._status_message = f"Выбрана модель {backend.model_id}"
            self._error_message = ""
        if previous is not backend:
            previous.unload()
        if self.state is AppState.ERROR_RECOVERABLE:
            self.state_machine.transition(AppState.IDLE, "installed model selected")
        return True

    def toggle_pause(self) -> bool:
        with self._lock:
            if self.state not in {
                AppState.LISTENING,
                AppState.SPEECH_DETECTED,
                AppState.TRANSCRIBING,
                AppState.PAUSED_TARGET_LOST,
            }:
                return False
            capture = self._capture
            audio_queue = self._audio_queue
            if not self._user_paused:
                self._user_paused = True
                self._status_message = "Пауза"
                if self.state is not AppState.PAUSED_TARGET_LOST:
                    self.state_machine.transition(
                        AppState.PAUSED_TARGET_LOST, "user paused capture"
                    )
            else:
                if audio_queue is None:
                    return False
                self._capture = self.capture_factory(audio_queue)
                capture = self._capture
                self._user_paused = False
                self._status_message = "Слушаю"
                self.state_machine.transition(AppState.LISTENING, "user resumed capture")
                capture.start()
                return True
        if capture is not None:
            capture.stop(drain_timeout=5.0)
        return True

    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            capture = self._capture
            stats = capture.stats() if capture is not None else self._last_stats
            started = self._session_started_monotonic
            elapsed = time.monotonic() - started if started is not None else 0.0
            target = self.target_manager.locked_target
            return ControllerSnapshot(
                state=self.state.name,
                language=self._language,
                session_elapsed_s=max(0.0, elapsed),
                queued_audio_seconds=stats.queued_audio_seconds if stats is not None else 0.0,
                last_committed_text=self._last_fragment,
                provisional_text=self._provisional,
                target_title=target.title if target is not None else "",
                input_rms=stats.current_input_rms if stats is not None else 0.0,
                clipping_percentage=stats.clipping_percentage if stats is not None else 0.0,
                warning=self._status_message,
                microphone_name=self.microphone_name,
                model_name=self.backend.model_id,
                model_profile=self.settings.dictation.model_profile,
                status_message=self._status_message,
                error_message=self._error_message,
            )

    def _handle_runtime_error(self, user_message: str, error: BaseException) -> None:
        self.logger.exception("runtime_error kind=%s", type(error).__name__, exc_info=error)
        with self._lock:
            self._error_message = user_message
            self._status_message = "Накопленный текст сохранён в журнале"
            capture = self._capture
            audio_queue = self._audio_queue
            session_id = self._session_id
        if capture is not None:
            capture.stop(drain_timeout=2.0)
        if audio_queue is not None:
            audio_queue.close()
        if session_id is not None:
            try:
                self.session_repository.add_warning(session_id, type(error).__name__)
            except (KeyError, OSError):
                self.logger.exception("session_warning_failed")
        if self.state not in {
            AppState.ERROR_RECOVERABLE,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }:
            try:
                self.state_machine.transition(
                    AppState.ERROR_RECOVERABLE, "runtime component failed"
                )
            except Exception:
                self.logger.exception("error_state_transition_failed")

    def _complete_session(self, status: str) -> None:
        pending = int(getattr(self.injection_service, "pending_fragments", 0))
        if status == "completed" and pending > 0 and not self._shutdown_requested.is_set():
            with self._lock:
                self._status_message = (
                    "Распознавание завершено; вернитесь в целевое окно для вставки"
                )
            delivery = threading.Thread(
                target=self._drain_pending_injection,
                name="voxtype-pending-injection",
                daemon=True,
            )
            with self._lock:
                existing = self._pending_delivery_thread
                if existing is not None and existing.is_alive():
                    return
                self._pending_delivery_thread = delivery
            delivery.start()
            return
        self._finalize_session(status)

    def _drain_pending_injection(self) -> None:
        while int(
            getattr(self.injection_service, "pending_fragments", 0)
        ) > 0 and not self._shutdown_requested.wait(0.25):
            result = self.injection_service.flush()
            if result.status is InjectionStatus.FAILED:
                with self._lock:
                    self._status_message = "Вставка не удалась; текст доступен в истории"
            elif result.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                with self._lock:
                    self._status_message = "Целевое окно неактивно; ожидаю возврата"
        if int(getattr(self.injection_service, "pending_fragments", 0)) == 0:
            self._finalize_session("completed")

    def flush_pending_text(self) -> bool:
        result = self.injection_service.flush()
        return result.status is InjectionStatus.INSERTED

    def copy_pending_text(self) -> str:
        result = self.injection_service.copy_pending_to_clipboard(consume=True)
        if result.status is InjectionStatus.COPIED and self.state is AppState.FINISHING:
            self._finalize_session("completed_safe_copy")
        return result.delivered_text

    def _finalize_session(self, status: str) -> None:
        with self._lock:
            session_id = self._session_id
            if session_id is None:
                return
            # Claim finalization before I/O so safe-copy and foreground polling
            # cannot finish the same SQLite session concurrently.
            self._session_id = None
            capture = self._capture
            spool = self._spool
            stats = capture.stats() if capture is not None else self._last_stats
        if session_id is not None:
            if stats is not None:
                self.session_repository.update_metrics(
                    session_id,
                    audio_duration_s=stats.processed_audio_seconds,
                    processing_s=float(self.metrics.snapshot()["latency_average_s"]),
                    dropped_frames=stats.dropped_frames,
                )
            self.session_repository.finish(session_id, status=status)
        self.target_manager.end_session()
        if spool is not None:
            spool.close()
            if spool.count == 0 and spool.directory.exists():
                try:
                    spool.directory.rmdir()
                except OSError:
                    self.logger.warning(
                        "empty_spool_directory_not_removed path=%s", spool.directory
                    )
        with self._lock:
            self._capture = None
            self._audio_queue = None
            self._spool = None
            self._segmenter = None
            self._journal = None
            self._session_started_monotonic = None
            self._pipeline_thread = None
            self._pending_delivery_thread = None
            self._status_message = "Готово" if status == "completed" else self._status_message
            self._user_paused = False
        if self.state is AppState.FINISHING:
            self.state_machine.transition(AppState.IDLE, "session drained and committed")

    def shutdown(self) -> None:
        self._shutdown_requested.set()
        if self.state in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
            AppState.PAUSED_TARGET_LOST,
        }:
            self.stop_dictation()
        with self._lock:
            finisher = self._finish_thread
        if finisher is not None and finisher is not threading.current_thread():
            finisher.join(timeout=30.0)
        if self.state is not AppState.SHUTTING_DOWN:
            if self.state is AppState.FINISHING:
                self._status_message = "Завершение продолжается в журнале"
            try:
                self.state_machine.transition(AppState.SHUTTING_DOWN, "application shutdown")
            except Exception:
                self.logger.exception("shutdown_transition_failed")
        self.injection_service.close()

    def _require_session_id(self) -> str:
        with self._lock:
            if self._session_id is None:
                raise RuntimeError("no active session")
            return self._session_id
