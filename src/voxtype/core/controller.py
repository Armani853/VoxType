"""Lifecycle controller joining capture, segmentation, ASR, journal and injection."""

from __future__ import annotations

import logging
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from queue import Empty, Full, Queue

import numpy as np

from voxtype.asr.base import ASRBackend, ASRModelUnavailable, TranscriptionRequest, bounded_prompt
from voxtype.asr.hallucination_guard import GuardedTranscription, HallucinationGuard
from voxtype.audio.capture import AudioCaptureService, AudioIngressOverflowError
from voxtype.audio.models import AudioQueueStats
from voxtype.audio.spool import AudioQueueLease, DiskSpool, LosslessAudioQueue, SpoolError
from voxtype.core.models import (
    AppPhase,
    AppReadiness,
    ControllerSnapshot,
    Language,
    ModelProfile,
    TranscriptFragment,
    WordStamp,
)
from voxtype.core.state_machine import AppState, StateMachine
from voxtype.core.profiles import DictationContext, DictationProfile, ProfileManager
from voxtype.monitoring.metrics import PerformanceMonitor
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.usage import UsageMetricsService
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings
from voxtype.transcript.commands import VoiceCommandProcessor
from voxtype.transcript.formatter import format_fragment_boundary
from voxtype.transcript.merger import TranscriptMerger, deduplicate_text_boundary
from voxtype.transcript.professional import (
    ProcessedDictation, ProfessionalContent, VoiceAction,
)
from voxtype.transcript.vocabulary import Vocabulary
from voxtype.transcript.workflows import WorkflowService
from voxtype.settings.enterprise import EnterprisePolicyService
from voxtype.settings.organization_pack import OrganizationPackService
from voxtype.vad.segmenter import AudioSegment, StreamingSegmenter
from voxtype.windows.injection import InjectionStatus, TextInjectionService
from voxtype.windows.target_window import TargetPolicy, TargetStatus, TargetWindowManager
from voxtype.windows.selection import SelectedText, SelectionProvider, SelectionUnavailable
from voxtype.windows.foreground import WindowMetadata, focused_control_type

CaptureFactory = Callable[[LosslessAudioQueue], AudioCaptureService]
SegmenterFactory = Callable[[], StreamingSegmenter]


class InferenceTimeoutError(RuntimeError):
    """A native inference call exceeded the bounded session deadline."""


class ProcessingCancelled(RuntimeError):
    """The user stopped processing pending audio."""


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
        retry_backend: ASRBackend | None = None,
        capture_factory: CaptureFactory,
        segmenter_factory: SegmenterFactory,
        target_manager: TargetWindowManager,
        injection_service: TextInjectionService,
        spool_root: Path,
        microphone_name: str = "Системный микрофон",
        vocabulary: Vocabulary | None = None,
        logger: logging.Logger | None = None,
        inference_timeout_s: float = 120.0,
    ) -> None:
        if inference_timeout_s <= 0:
            raise ValueError("inference_timeout_s must be positive")
        self.settings_repository = settings_repository
        self.session_repository = session_repository
        self.backend = backend
        self.retry_backend = retry_backend
        self.capture_factory = capture_factory
        self.segmenter_factory = segmenter_factory
        self.target_manager = target_manager
        self.injection_service = injection_service
        self.spool_root = spool_root.resolve()
        self.spool_root.mkdir(parents=True, exist_ok=True)
        self.microphone_name = microphone_name
        self._microphone_available = True
        self._model_available = True
        self._initialized = False
        loaded_settings = self.settings_repository.load()
        self.usage_metrics = UsageMetricsService(
            session_repository, settings_repository, loaded_settings.professional.get("typing_speed_wpm"),
        )
        self.profiles = ProfileManager(settings_repository, loaded_settings.professional)
        self.professional_content = ProfessionalContent(settings_repository, loaded_settings.professional)
        self.organization_packs = OrganizationPackService(
            settings_repository, self.profiles, self.professional_content,
        )
        self.enterprise = EnterprisePolicyService()
        self.policy = self.enterprise.policy
        if self.policy.disable_history and isinstance(injection_service, TextInjectionService):
            injection_service.configure_pending_storage(False)
        if self.policy.disabled_applications:
            self.target_manager.application_blocked = (
                lambda window: self.policy.blocked(DictationContext.capture(window, "locked"))
            )
        self.workflows = WorkflowService(settings_repository, session_repository, loaded_settings.professional)
        self.professional_action: Callable[[int, VoiceAction, DictationProfile], None] | None = None
        self.profile_backend_factory: Callable[[str], ASRBackend] | None = None
        self._session_profile = self.profiles.resolve(DictationContext())
        self._session_context = DictationContext()
        self._session_backend: ASRBackend | None = None
        self._professional_session_number = 0
        self._session_text_pipeline = self.professional_content.snapshot()
        self._command_mode = False
        self._last_processed: ProcessedDictation | None = None
        self._previous_completed_text = ""
        self._file_recognition_active = False
        self._file_recognition_cancel = threading.Event()
        self._file_backend: ASRBackend | None = None
        self.vocabulary = vocabulary or Vocabulary(loaded_settings.dictation.custom_vocabulary)
        self.logger = logger or logging.getLogger("voxtype.controller")
        self.inference_timeout_s = float(inference_timeout_s)
        self.state_machine = StateMachine(logger=self.logger)
        self.metrics = PerformanceMonitor()
        self.guard = HallucinationGuard()
        self.merger = TranscriptMerger()
        self.settings: AppSettings = loaded_settings
        self._language = self.settings.dictation.language
        self._lock = threading.RLock()
        self._capture: AudioCaptureService | None = None
        self._audio_queue: LosslessAudioQueue | None = None
        self._spool: DiskSpool | None = None
        self._segmenter: StreamingSegmenter | None = None
        self._journal: SessionJournal | None = None
        self._session_id: str | None = None
        self._session_started_monotonic: float | None = None
        self._session_stopped_monotonic: float | None = None
        self._completed_duration_s = 0.0
        self._completed_transcript = ""
        self._delivery_status = "pending"
        self._capture_started_monotonic: float | None = None
        self._pipeline_thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None
        self._recovery_active = False
        self._start_thread: threading.Thread | None = None
        self._preload_thread: threading.Thread | None = None
        self._stop_thread: threading.Thread | None = None
        self._pause_thread: threading.Thread | None = None
        self._finish_thread: threading.Thread | None = None
        self._pending_delivery_thread: threading.Thread | None = None
        self._language_switches: deque[tuple[float, Language]] = deque()
        self._pause_intervals: deque[tuple[float, float | None]] = deque()
        self._worker_language = self._language
        self._user_paused = False
        self._last_fragment = ""
        self._provisional = ""
        self._status_message = ""
        self._error_message = ""
        self._last_stats: AudioQueueStats | None = None
        self._processing_seconds = 0.0
        self._prompt_language: Language | None = None
        self._last_prompt_segment_end_s: float | None = None
        self._last_prompt_result_uncertain = False
        self._audio_archive: wave.Wave_write | None = None
        self._shutdown_requested = threading.Event()
        self._start_cancelled = threading.Event()
        self._processing_cancelled = threading.Event()
        self._delivery_busy = False
        self._delivery_queue: Queue[tuple[str, str] | None] = Queue(maxsize=256)
        self._delivery_thread = threading.Thread(
            target=self._delivery_loop,
            name="voxtype-text-delivery",
            daemon=True,
        )
        self._delivery_thread.start()

    @property
    def state(self) -> AppState:
        return self.state_machine.state

    @property
    def language(self) -> Language:
        with self._lock:
            return self._language

    @property
    def user_paused(self) -> bool:
        with self._lock:
            return self._user_paused

    def initialize(self, *, preload_model: bool = False) -> None:
        if self.state is not AppState.STARTING:
            return
        self._initialized = True
        if preload_model:
            self.state_machine.transition(AppState.MODEL_LOADING, "preload installed model")
            thread = threading.Thread(
                target=self._preload_model, name="voxtype-model-loader", daemon=True
            )
            self._preload_thread = thread
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
        if current in {AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE}:
            self.start_dictation()
        elif current in {
            AppState.STARTING,
            AppState.MODEL_LOADING,
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
            AppState.PAUSED,
            AppState.PAUSED_TARGET_LOST,
        }:
            self.stop_dictation()

    def start_dictation(self) -> bool:
        with self._lock:
            if self._shutdown_requested.is_set() or self._recovery_active or self._file_recognition_active:
                return False
            if (
                self._session_id is not None
                or self._inference_thread is not None
                or self._delivery_busy
                or any(
                    worker is not None and worker.is_alive()
                    for worker in (self._start_thread, self._stop_thread, self._finish_thread)
                )
            ):
                return False
            if self.state not in {
                AppState.IDLE,
                AppState.COMPLETED,
                AppState.ERROR_RECOVERABLE,
            }:
                return False
            self._start_cancelled.clear()
            self._processing_cancelled.clear()
            decision = self.target_manager.start_session()
            target = getattr(decision, "target", None)
            context = DictationContext.capture(target, self.settings.dictation.target_mode.value)
            if isinstance(target, WindowMetadata):
                context = replace(context, control_type=focused_control_type(target))
            if self.policy.blocked(context) or (
                self.policy.disabled_applications
                and self.policy.blocked(DictationContext.capture(getattr(decision, "foreground", None), "locked"))
            ):
                self._error_message = "Диктовка в этом приложении запрещена политикой организации"
                self._status_message = self._error_message
                self.target_manager.end_session()
                return False
            self._session_profile = self.profiles.resolve(context)
            if not self.profile_allowed(self._session_profile):
                self._error_message = "Выбранный профиль или его модель запрещены политикой организации"
                self._status_message = self._error_message
                self.target_manager.end_session()
                return False
            if isinstance(self.injection_service, TextInjectionService) and not self.injection_service.configure_pending_storage(
                self.settings.privacy.save_history and not self.policy.disable_history
            ):
                self._error_message = "Скопируйте ожидающий доставки текст перед началом записи"
                self.target_manager.end_session()
                return False
            self._session_context = replace(context, profile_id=self._session_profile.id)
            self._professional_session_number += 1
            self._session_text_pipeline = self.professional_content.snapshot()
            if not self.policy.allow_user_snippets:
                self._session_text_pipeline = replace(self._session_text_pipeline, snippets=())
            self._command_mode = self.settings.dictation.voice_commands
            self._last_processed = None
            if self.state is AppState.ERROR_RECOVERABLE:
                self.state_machine.transition(AppState.IDLE, "retry requested")
            self.state_machine.transition(AppState.STARTING, "user requested dictation")
            self._session_started_monotonic = time.monotonic()
            self._session_stopped_monotonic = None
            self._completed_duration_s = 0.0
            self._completed_transcript = ""
            self._last_stats = None
            self._delivery_status = "pending"
            self._last_fragment = ""
            self._provisional = ""
            self._error_message = ""
            self._status_message = "Запускаю…"
            self._user_paused = False
            self._processing_seconds = 0.0
            self._prompt_language = None
            self._last_prompt_segment_end_s = None
            self._last_prompt_result_uncertain = False
            self._pause_intervals.clear()
            self.merger.reset()
            starter = threading.Thread(
                target=self._prepare_and_start_runtime,
                args=(decision,),
                name="voxtype-session-starter",
                daemon=True,
            )
            self._start_thread = starter
            starter.start()
        return True

    def _prepare_and_start_runtime(self, decision: object) -> None:
        """Build disk/audio/model runtime away from the Qt and hotkey threads."""

        try:
            target = getattr(decision, "target", None)
            target_name = target.process_name if target is not None else ""
            profile = self.settings.dictation.model_profile
            professional = self._session_profile
            language = Language(professional.language) if professional.language else self._language
            backend = self.backend
            if professional.model_id and professional.model_id != backend.model_id:
                alternate = self._file_backend
                if alternate is not None and alternate.model_id == professional.model_id:
                    backend = alternate
                elif self.profile_backend_factory is None:
                    raise ASRModelUnavailable("Profile model is unavailable")
                else:
                    backend = self.profile_backend_factory(professional.model_id)
            session_id = self.session_repository.create(
                language,
                backend.model_id,
                profile,
                target_name,
                profile_id=professional.id,
            )
            journal = SessionJournal(
                self.session_repository, session_id,
                persist_text=self.settings.privacy.save_history and not self.policy.disable_history,
            )
            journal.append(
                "session_started",
                {
                    "target_status": getattr(getattr(decision, "status", None), "value", "unknown"),
                    "language": language.value,
                    "model": backend.model_id,
                    "profile_id": professional.id,
                },
            )
            self.logger.info(
                "session_started session_id=%s language=%s model=%s profile=%s target_process=%s",
                session_id,
                language.value,
                backend.model_id,
                profile.value,
                target_name or "none",
            )
            audio_archive: wave.Wave_write | None = None
            if self.settings.privacy.save_audio and not self.policy.disable_audio_storage:
                audio_directory = self.session_repository.path.parent / "audio"
                audio_directory.mkdir(parents=True, exist_ok=True)
                audio_archive = wave.open(  # noqa: SIM115 - session owns it until finalization
                    str(audio_directory / f"{session_id}.wav"), "wb"
                )
                audio_archive.setnchannels(1)
                audio_archive.setsampwidth(2)
                audio_archive.setframerate(16_000)
            session_spool = self.spool_root / session_id
            spool = (
                None if self.policy.disable_audio_storage
                else DiskSpool(session_spool, max_bytes=4 * 1024**3)
            )
            audio_queue = LosslessAudioQueue(
                max_ram_chunks=4096 if spool is None else 128,
                spool=spool,
                spill_threshold=0.70,
                max_inflight=2048,
                durable=spool is not None,
            )
            segmenter = self.segmenter_factory()
            capture = self.capture_factory(audio_queue)
            with self._lock:
                self._session_id = session_id
                self._journal = journal
                self._audio_archive = audio_archive
                self._spool = spool
                self._audio_queue = audio_queue
                self._segmenter = segmenter
                self._capture = capture
                self._worker_language = language
                self._session_backend = backend
                self._language_switches.clear()
            if self._start_cancelled.is_set() or self.state is AppState.FINISHING:
                audio_queue.close()
                self._finalize_session("cancelled")
                return
            self.state_machine.transition_if(
                {AppState.STARTING}, AppState.MODEL_LOADING, "session runtime prepared"
            )
            with self._lock:
                if self.state is not AppState.MODEL_LOADING:
                    audio_queue.close()
                    self._finalize_session("cancelled")
                    return
                self._status_message = "Подготовка модели"
            self._load_and_start_runtime()
        except Exception as exc:
            self._handle_runtime_error("Не удалось начать диктовку", exc)

    def _load_and_start_runtime(self) -> None:
        try:
            if self._start_cancelled.is_set():
                self._finalize_session("cancelled")
                return
            (self._session_backend or self.backend).load()
            with self._lock:
                if self._start_cancelled.is_set():
                    self._finalize_session("cancelled")
                    return
                if self._shutdown_requested.is_set():
                    raise RuntimeError("shutdown requested while loading model")
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
            # PortAudio can block inside a driver. Never hold the controller lock
            # while opening the stream: the GUI needs that lock for snapshot().
            capture.start()
            with self._lock:
                cancelled = (
                    self._start_cancelled.is_set()
                    or self._shutdown_requested.is_set()
                    or self.state is not AppState.MODEL_LOADING
                )
                if not cancelled:
                    self._capture_started_monotonic = time.monotonic()
                    self._status_message = "Слушаю"
                    self.state_machine.transition(AppState.IDLE, "model ready for session")
                    self.state_machine.transition(AppState.LISTENING, "microphone capture started")
            if cancelled:
                capture.stop(drain_timeout=2.0)
                audio_queue.close()
                self._finalize_session("cancelled")
        except Exception as exc:
            self._handle_runtime_error("Не удалось начать диктовку", exc)

    def stop_dictation(self) -> bool:
        with self._lock:
            if self.state not in {
                AppState.STARTING,
                AppState.MODEL_LOADING,
                AppState.LISTENING,
                AppState.SPEECH_DETECTED,
                AppState.TRANSCRIBING,
                AppState.PAUSED,
                AppState.PAUSED_TARGET_LOST,
            }:
                return False
            was_starting = self.state in {AppState.STARTING, AppState.MODEL_LOADING}
            self._start_cancelled.set()
            self._session_stopped_monotonic = time.monotonic()
            self.state_machine.transition(AppState.FINISHING, "user stopped dictation")
            self._status_message = (
                "Отменяю запуск" if was_starting else "Обрабатываю оставшийся звук"
            )
            capture = self._capture
            audio_queue = self._audio_queue
            if was_starting:
                return True
            stopper = threading.Thread(
                target=self._stop_capture_and_drain,
                args=(capture, audio_queue),
                name="voxtype-capture-stopper",
                daemon=True,
            )
            self._stop_thread = stopper
            stopper.start()
        return True

    def _stop_capture_and_drain(
        self,
        capture: AudioCaptureService | None,
        audio_queue: LosslessAudioQueue | None,
    ) -> None:
        if capture is not None:
            stopped = capture.stop(drain_timeout=10.0)
            with self._lock:
                self._last_stats = capture.stats()
            if not stopped:
                self._handle_runtime_error(
                    "Не удалось безопасно остановить микрофон",
                    RuntimeError("audio dispatcher did not stop after drain timeout"),
                )
                return
            fatal_error = getattr(capture, "fatal_error", None)
            if fatal_error is not None:
                self._handle_runtime_error(
                    "Аудиопоток завершён с ошибкой; текст сохранён",
                    fatal_error,
                )
                return
        if audio_queue is not None:
            audio_queue.close()
        finisher = threading.Thread(
            target=self._wait_for_pipeline, name="voxtype-session-finisher", daemon=True
        )
        with self._lock:
            self._finish_thread = finisher
            finisher.start()

    def _wait_for_pipeline(self) -> None:
        with self._lock:
            pipeline = self._pipeline_thread
        if pipeline is not None and pipeline is not threading.current_thread():
            while pipeline.is_alive():
                pipeline.join(timeout=0.25)
                if self._shutdown_requested.is_set() and pipeline.is_alive():
                    self._status_message = "Ожидаю завершения локального inference"
        self._delivery_queue.join()
        with self._lock:
            cancelled = self._processing_cancelled.is_set() and self._session_id is not None
        if cancelled:
            self._handle_runtime_error("Обработка отменена", ProcessingCancelled())
            return
        # The capture-stop snapshot may still include audio awaiting the pipeline.
        # Refresh it only after all leases have been acknowledged so a completed
        # session does not keep showing a stale audio backlog.
        with self._lock:
            capture = self._capture
        if capture is not None:
            final_stats = capture.stats()
            with self._lock:
                self._last_stats = final_stats
        # Delivery warnings are tracked separately from ASR/session failures.
        # A temporarily inactive target must not mark recognized speech as an
        # errored history session.
        self._complete_session("completed")

    def cancel_processing(self) -> bool:
        with self._lock:
            if self.state is not AppState.FINISHING or self._session_id is None:
                return False
            self._processing_cancelled.set()
            self._status_message = "Останавливаю обработку; записанный текст сохранён"
            return True

    def _pipeline_loop(self) -> None:
        with self._lock:
            audio_queue = self._audio_queue
            segmenter = self._segmenter
        if audio_queue is None or segmenter is None:
            self._handle_runtime_error("Внутренняя аудиоочередь не создана", RuntimeError())
            return
        pending: deque[AudioQueueLease] = deque()
        pending_seconds = 0.0
        input_overflow_reported = False

        def ack_pending() -> None:
            nonlocal pending_seconds
            while pending:
                audio_queue.ack(pending.popleft())
            pending_seconds = 0.0

        def ack_committed_prefix() -> None:
            """Keep only leases whose PCM is still buffered by the segmenter."""
            nonlocal pending_seconds
            retained_seconds = segmenter.buffered_seconds
            while pending:
                oldest_seconds = pending[0].chunk.duration_seconds
                if pending_seconds - oldest_seconds + 1e-9 < retained_seconds:
                    break
                audio_queue.ack(pending.popleft())
                pending_seconds -= oldest_seconds

        try:
            while True:
                if self._processing_cancelled.is_set():
                    raise ProcessingCancelled()
                lease = audio_queue.acquire(timeout=0.1)
                if lease is None:
                    with self._lock:
                        capture = self._capture
                    fatal_error = None if capture is None else getattr(capture, "fatal_error", None)
                    if fatal_error is not None:
                        raise fatal_error
                    if audio_queue.closed:
                        break
                    continue
                if lease.chunk.input_overflow and not input_overflow_reported:
                    input_overflow_reported = True
                    self.logger.warning("audio_input_overflow session_id=%s", self._session_id)
                    with self._lock:
                        self._status_message = "Микрофон потерял часть звука; запись продолжается"
                    if self._session_id is not None:
                        self.session_repository.add_warning(self._session_id, "AudioInputOverflow")
                if self._captured_during_pause(lease.chunk.captured_at):
                    audio_queue.ack(lease)
                    continue
                if self._apply_language_switches(lease.chunk.captured_at, segmenter):
                    ack_pending()
                pending.append(lease)
                pending_seconds += lease.chunk.duration_seconds
                archive = self._audio_archive
                if archive is not None:
                    pcm16 = (np.clip(lease.chunk.samples, -1.0, 1.0) * np.float32(32_767.0)).astype(
                        "<i2"
                    )
                    archive.writeframesraw(pcm16.tobytes())
                segments = segmenter.process(lease.chunk.samples)
                for segment in segments:
                    self._transcribe_segment(segment, self._worker_language)
                ack_committed_prefix()
            tail = segmenter.flush()
            for segment in tail:
                self._transcribe_segment(segment, self._worker_language)
            ack_pending()
        except Exception as exc:
            while pending:
                failed_lease = pending.pop()
                try:
                    audio_queue.nack(failed_lease)
                except (ValueError, OSError):
                    self.logger.exception("audio_lease_nack_failed")
            self._handle_runtime_error("Ошибка локального распознавания", exc)

    def _apply_language_switches(self, captured_at: float, segmenter: StreamingSegmenter) -> bool:
        committed = False
        while True:
            with self._lock:
                if not self._language_switches or captured_at < self._language_switches[0][0]:
                    return committed
                _switch_time, language = self._language_switches.popleft()
            for segment in segmenter.flush():
                self._transcribe_segment(segment, self._worker_language)
                committed = True
            segmenter.reset(preserve_sequence=True)
            self._worker_language = language

    def _transcribe_segment(self, segment: AudioSegment, language: Language) -> None:
        if not segment.samples.size:
            return
        self.state_machine.transition_if(
            {AppState.LISTENING}, AppState.SPEECH_DETECTED, "VAD emitted speech"
        )
        self.state_machine.transition_if(
            {AppState.SPEECH_DETECTED}, AppState.TRANSCRIBING, "ASR inference started"
        )
        committed = self.merger.committed_text
        with self._lock:
            capture = self._capture
        backlog = capture.stats().queued_audio_seconds if capture is not None else 0.0
        with self._lock:
            if backlog >= 10.0:
                self._status_message = "Обработка немного отстаёт; звук сохранён в очереди"
            elif backlog < 5.0 and self._status_message.startswith("Обработка немного отстаёт"):
                self._status_message = "Распознаю"
        professional_request = self._session_text_pipeline.request(
            self._professional_session_number, language.value,
            (self._session_backend or self.backend).model_id, self._session_profile,
            self._session_context, self.vocabulary.entries,
        )
        request = TranscriptionRequest(
            audio=segment.samples,
            sample_rate=segment.sample_rate,
            language=language.value,
            start_offset_seconds=segment.start_seconds,
            initial_prompt=bounded_prompt(
                self._safe_prompt_context(segment, language, committed),
                professional_request.hotwords,
            ),
            beam_size=1 if backlog >= 10.0 else None,
            condition_on_previous_text=False,
            metadata={"segment_sequence": str(segment.sequence)},
        )
        with self._lock:
            self._provisional = "Распознаю…"
        inference_started = time.perf_counter()
        guarded = self._transcribe_guarded_with_timeout(
            request, committed, cancel_event=self._processing_cancelled
        )
        if self._processing_cancelled.is_set():
            raise ProcessingCancelled()
        result = guarded.result
        with self._lock:
            self._last_prompt_result_uncertain = result.possible_inaccuracy
        self.logger.info(
            "chunk_inference session_id=%s sequence=%d model=%s backend=%s audio_s=%.3f "
            "inference_s=%.3f rtf=%.3f queue_s=%.3f wall_s=%.3f retry_s=%.3f",
            self._session_id or "none",
            segment.sequence,
            result.model_id,
            result.backend_name,
            result.audio_duration_seconds,
            result.processing_seconds,
            result.processing_seconds / max(result.audio_duration_seconds, 1e-9),
            backlog,
            time.perf_counter() - inference_started,
            guarded.retry_wall_seconds,
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
            text=result.text,
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
        processed: ProcessedDictation | None = None
        deferred_action: VoiceAction | None = None

        def transform(novel_text: str) -> str:
            nonlocal processed, deferred_action
            processed = self._session_text_pipeline.process(
                professional_request, novel_text, self._session_profile,
                command_mode=self._command_mode,
                allow_commands=self.policy.allow_voice_commands,
            )
            action = processed.voice_action
            if action is None:
                return processed.final_text
            if action.action_type == "InsertSnippet" and not self.policy.allow_user_snippets:
                return processed.normalized_text
            inline = self._session_text_pipeline.inline_action(action, self._session_profile)
            if inline is not None:
                return inline
            if self.professional_action is not None:
                deferred_action = action
                return ""
            return processed.normalized_text

        mode = self._session_profile.formatting_mode
        outcome = self.merger.merge(
            fragment,
            add_space=self.settings.dictation.add_fragment_space,
            capitalize=self.settings.dictation.capitalize_sentences and mode not in {"code", "verbatim"},
            code_spacing=mode == "code",
            transform=transform,
            preserve_format=mode in {"code", "verbatim"},
        )
        if processed is not None:
            with self._lock:
                self._last_processed = replace(
                    processed, raw_text=result.text, final_text=outcome.insertion_text
                )
        with self._lock:
            capture_started = self._capture_started_monotonic
        end_to_end_latency = result.processing_seconds
        if capture_started is not None:
            end_to_end_latency = max(
                result.processing_seconds,
                time.monotonic() - (capture_started + segment.end_seconds),
            )
        self.metrics.record_processed(
            result.audio_duration_seconds,
            result.processing_seconds,
            end_to_end_latency,
            model_id=result.model_id,
            retried=guarded.retried,
            cascade_retry=(
                guarded.retry_model_id is not None
                and guarded.retry_model_id != self.backend.model_id
            ),
            uncertain=result.possible_inaccuracy,
        )
        with self._lock:
            self._processing_seconds += result.processing_seconds
        journal = self._journal
        if journal is not None and journal.persist_text and result.text.strip():
            journal.append("raw_asr", {
                "schema_version": 1, "segment_sequence": segment.sequence, "text": result.text,
            })
        if deferred_action is not None and self.professional_action is not None:
            self.professional_action(professional_request.session_id, deferred_action, self._session_profile)
        if guarded.assessment.suspicious and journal is not None:
            warning = ",".join(issue.value for issue in guarded.assessment.issues)
            self.session_repository.add_warning(
                self._require_session_id(), warning, suspicious=True
            )
        if outcome.insertion_text:
            if journal is None:
                raise RuntimeError("committed text has no active journal")
            journal.commit_text(outcome.insertion_text)
            with self._lock:
                self._last_fragment = outcome.insertion_text.strip()
                self._provisional = ""
                if not self.settings.dictation.auto_insert:
                    self._delivery_status = "pending"
                    self._status_message = (
                        "Текст сохранён в истории; автовставка выключена" if journal.persist_text
                        else "Текст доступен в результате; автовставка выключена"
                    )
                else:
                    self._delivery_status = "pending"
                    self._status_message = "Подтверждённый текст ожидает вставки"
            if self.settings.dictation.auto_insert:
                fragment_id = f"{journal.session_id}:{segment.sequence}"
                try:
                    self._delivery_queue.put_nowait((fragment_id, outcome.insertion_text))
                except Full:
                    with self._lock:
                        self._delivery_status = "failed"
                        self._error_message = (
                            "Очередь вставки заполнена; весь текст сохранён в результате"
                        )
        else:
            with self._lock:
                self._provisional = ""
        self.state_machine.transition_if(
            {AppState.TRANSCRIBING, AppState.SPEECH_DETECTED},
            AppState.LISTENING,
            "segment committed",
        )

    def _delivery_loop(self) -> None:
        """Single bounded FIFO owner for every automatic target insertion."""

        while True:
            item = self._delivery_queue.get()
            owns_delivery = False
            try:
                if item is None:
                    return
                fragment_id, text = item
                with self._lock:
                    if (
                        self._processing_cancelled.is_set()
                        or self._session_id != fragment_id.rsplit(":", 1)[0]
                    ):
                        continue
                    self._delivery_busy = True
                    owns_delivery = True
                try:
                    injection = (
                        self.injection_service.submit(
                            text,
                            fragment_id=fragment_id,
                            exact_text=self._session_profile.formatting_mode in {"code", "verbatim"},
                            strategy=self._session_profile.insertion_mode or None,
                        )
                        if isinstance(self.injection_service, TextInjectionService)
                        else self.injection_service.submit(text)
                    )
                except Exception as exc:
                    self.logger.warning("delivery_failed kind=%s", type(exc).__name__)
                    with self._lock:
                        self._delivery_status = "failed"
                        self._error_message = "Вставка не удалась; весь текст сохранён"
                    continue
                with self._lock:
                    if self._processing_cancelled.is_set():
                        continue
                    if injection.status in {
                        InjectionStatus.QUEUED,
                        InjectionStatus.BLOCKED,
                    }:
                        self._delivery_status = "queued_target_inactive"
                        reason = self._target_warning(injection.target_status)
                        self._status_message = reason
                        self._error_message = (
                            f"{reason}. Вернитесь в целевое окно или скопируйте весь текст."
                        )
                    elif injection.status in {
                        InjectionStatus.FAILED,
                        InjectionStatus.UNKNOWN,
                    }:
                        self._delivery_status = "failed"
                        self._status_message = "Вставка не удалась; текст сохранён"
                        self._error_message = (
                            "Вставка не подтверждена. Автоповтор отключён во избежание дубля."
                        )
                    elif injection.status is InjectionStatus.INSERTED:
                        self._delivery_status = "delivered"
                        self._error_message = ""
                    elif injection.status is InjectionStatus.COPIED:
                        self._delivery_status = "copied"
                        self._error_message = ""
                if injection.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                    self.state_machine.transition_if(
                        {
                            AppState.LISTENING,
                            AppState.SPEECH_DETECTED,
                            AppState.TRANSCRIBING,
                        },
                        AppState.PAUSED_TARGET_LOST,
                        "target unavailable for injection",
                    )
                elif not self._user_paused:
                    self.state_machine.transition_if(
                        {AppState.PAUSED_TARGET_LOST},
                        AppState.LISTENING,
                        "target became available",
                    )
            finally:
                if owns_delivery:
                    with self._lock:
                        self._delivery_busy = False
                self._delivery_queue.task_done()

    def _safe_prompt_context(
        self,
        segment: AudioSegment,
        language: Language,
        committed_text: str,
        *,
        reset_after_s: float = 8.0,
    ) -> str:
        """Return confirmed context only across a short same-language continuation."""

        with self._lock:
            previous_language = self._prompt_language
            previous_end = self._last_prompt_segment_end_s
            previous_uncertain = self._last_prompt_result_uncertain
            self._prompt_language = language
            self._last_prompt_segment_end_s = segment.end_seconds
        if previous_uncertain:
            return ""
        if previous_language is not None and previous_language is not language:
            return ""
        if previous_end is not None and segment.start_seconds - previous_end > reset_after_s:
            return ""
        if committed_text.rstrip().endswith((".", "!", "?", "…")):
            return ""
        return committed_text

    def switch_language(self) -> Language:
        switch_time = time.time()
        with self._lock:
            new_language = self._language.toggled()
            self._language = new_language
            self.settings.dictation.language = new_language
            self.settings_repository.save(self.settings)
            if self._session_id is not None and not self._session_profile.language:
                self._language_switches.append((switch_time, new_language))
                if self._journal is not None:
                    self._journal.append(
                        "language_switch",
                        {"language": new_language.value, "captured_at": switch_time},
                    )
            return new_language

    def _transcribe_guarded_with_timeout(
        self,
        request: TranscriptionRequest,
        committed_text: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> GuardedTranscription:
        """Run native inference behind one daemon watchdog per serial chunk."""

        results: Queue[GuardedTranscription | BaseException] = Queue(maxsize=1)
        backend = (self._session_backend if cancel_event is not None else None) or self.backend
        retry_backend = self.retry_backend if backend is self.backend else None
        if retry_backend is not None and not self.policy.model_allowed(retry_backend.model_id):
            retry_backend = None

        def worker() -> None:
            try:
                outcome: GuardedTranscription | BaseException = self.guard.transcribe_guarded(
                    backend,
                    request,
                    committed_text=committed_text,
                    retry_backend=retry_backend,
                )
            except BaseException as exc:  # propagate native/backend failures to pipeline owner
                outcome = exc
            finally:
                with self._lock:
                    self._inference_thread = None
            results.put_nowait(outcome)

        thread = threading.Thread(target=worker, name="voxtype-inference-call", daemon=True)
        with self._lock:
            if self._inference_thread is not None:
                raise InferenceTimeoutError("previous native inference still owns the model")
            self._inference_thread = thread
            try:
                thread.start()
            except RuntimeError:
                self._inference_thread = None
                raise
        deadline = time.monotonic() + self.inference_timeout_s
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ProcessingCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InferenceTimeoutError(
                    f"local inference exceeded {self.inference_timeout_s:.1f} seconds"
                )
            try:
                outcome = results.get(timeout=min(remaining, 0.1) if cancel_event else remaining)
                break
            except Empty:
                continue
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def configure_backend(
        self,
        backend: ASRBackend,
        profile: ModelProfile,
        retry_backend: ASRBackend | None = None,
    ) -> bool:
        """Switch an installed model only while no session owns the current one."""
        with self._lock:
            if (
                not self.policy.model_allowed(backend.model_id)
                or self._shutdown_requested.is_set()
                or self._recovery_active
                or self._file_recognition_active
                or self._inference_thread is not None
                or self._session_id is not None
                or self.state not in {AppState.IDLE, AppState.ERROR_RECOVERABLE}
            ):
                return False
            previous = self.backend
            previous_retry = self.retry_backend
            self.backend = backend
            self._model_available = True
            self.retry_backend = retry_backend
            self.settings.dictation.model_profile = profile
            self.settings.model.installed_model = backend.model_id
            self.settings_repository.save(self.settings)
            self._status_message = f"Выбрана модель {backend.model_id}"
            self._error_message = ""
        if previous is not backend:
            previous.unload()
        if (
            previous_retry is not None
            and previous_retry is not backend
            and previous_retry is not retry_backend
        ):
            previous_retry.unload()
        if self.state is AppState.ERROR_RECOVERABLE:
            self.state_machine.transition(AppState.IDLE, "installed model selected")
        return True

    def profile_allowed(self, profile: DictationProfile) -> bool:
        return (
            profile.enabled and self.policy.profile_allowed(profile.id)
            and self.policy.model_allowed(profile.model_id or self.backend.model_id)
        )

    def reserve_file_recognition(self) -> threading.Event | None:
        with self._lock:
            if (
                self._shutdown_requested.is_set()
                or self._recovery_active
                or self._file_recognition_active
                or self._session_id is not None
                or self._inference_thread is not None
                or self.state not in {AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE}
                or any(
                    worker is not None and worker.is_alive()
                    for worker in (self._start_thread, self._stop_thread, self._finish_thread,
                                   self._preload_thread)
                )
            ):
                return None
            self._file_recognition_active = True
            self._file_recognition_cancel.clear()
            return self._file_recognition_cancel

    def release_file_recognition(self) -> None:
        with self._lock:
            self._file_recognition_active = False

    def transcribe_professional_file(self, source: Path, profile_id: str, language: str, job_id: str):
        from voxtype.asr.file_transcription import FileTranscriptionService

        profile = next((item for item in self.profiles.profiles()
                        if item.id == profile_id and self.profile_allowed(item)), None)
        if profile is None:
            raise ValueError("Профиль задания недоступен")
        backend = self.backend
        if profile.model_id and profile.model_id != backend.model_id:
            if self.profile_backend_factory is None:
                raise ASRModelUnavailable("Profile model is unavailable")
            previous = self._file_backend
            if previous is None or previous.model_id != profile.model_id:
                if previous is not None:
                    previous.unload()
                self._file_backend = self.profile_backend_factory(profile.model_id)
            backend = self._file_backend
        language_preference = language if language in {"ru", "en"} else profile.language
        selected_language = "en" if language_preference == "en" else "ru"
        pipeline = self.professional_content.snapshot()
        context = DictationContext(selection_mode="file", profile_id=profile.id)
        request = pipeline.request(
            int(job_id[:16], 16), selected_language, backend.model_id, profile, context,
            self.settings.dictation.custom_vocabulary,
        )
        return FileTranscriptionService().transcribe(
            source, backend, selected_language, self._file_recognition_cancel,
            initial_prompt=request.initial_prompt or "",
            transform=lambda text: pipeline.process(
                request, text, profile, allow_commands=False
            ).final_text,
        )

    def journal_file_result(
        self, job_id: str, profile_id: str, language: str, duration: float, text: str,
    ) -> None:
        if self.policy.disable_history or not self.settings.privacy.save_history or not text.strip():
            return
        profile = next((item for item in self.profiles.profiles() if item.id == profile_id), None)
        self.session_repository.save_file_record(
            job_id, language, (profile.model_id if profile else "") or self.backend.model_id,
            profile_id, self.settings.dictation.model_profile.value, duration, text,
        )

    def recover_session(self, session_id: str) -> str:
        """Replay crash-safe PCM and append only the missing transcript tail."""

        with self._lock:
            if (
                self._shutdown_requested.is_set()
                or self._recovery_active
                or self._inference_thread is not None
                or self._session_id is not None
                or self._file_recognition_active
                or self.policy.disable_history
                or self.policy.disable_audio_storage
                or self.state not in {AppState.IDLE, AppState.ERROR_RECOVERABLE}
            ):
                raise RuntimeError("active dictation must finish before recovery")
            self._recovery_active = True
        try:
            return self._recover_session(session_id)
        finally:
            with self._lock:
                self._recovery_active = False

    def _recover_session(self, session_id: str) -> str:
        self.logger.info("session_recovery_started session_id=%s", session_id)
        record = self.session_repository.get(session_id)
        if record is None:
            raise KeyError(session_id)
        spool_directory = self.spool_root / session_id
        if not spool_directory.exists():
            self.session_repository.finish(session_id, status="recovered")
            return record.committed_text

        spool = DiskSpool(spool_directory, max_bytes=4 * 1024**3)
        segmenter = self.segmenter_factory()
        merger = TranscriptMerger()
        language = Language(record.language)
        switches = deque(self.session_repository.language_switches(session_id))
        leased_sequences: list[int] = []
        try:
            self.backend.load()
            while (chunk := spool.acquire()) is not None:
                leased_sequences.append(chunk.sequence)
                while switches and chunk.captured_at >= switches[0][0]:
                    for segment in segmenter.flush():
                        self._merge_recovered_segment(
                            segment, language, record.committed_text, merger
                        )
                    segmenter.reset(preserve_sequence=True)
                    _captured_at, language_code = switches.popleft()
                    language = Language(language_code)
                for segment in segmenter.process(chunk.samples):
                    self._merge_recovered_segment(segment, language, record.committed_text, merger)
            for segment in segmenter.flush():
                self._merge_recovered_segment(segment, language, record.committed_text, merger)

            recovered = deduplicate_text_boundary(record.committed_text, merger.committed_text)
            insertion = format_fragment_boundary(
                record.committed_text,
                recovered,
                add_space=self.settings.dictation.add_fragment_space,
                capitalize=self.settings.dictation.capitalize_sentences,
            )
            persists = self.settings.privacy.save_history and not self.policy.disable_history
            if insertion and persists:
                self.session_repository.commit_next_text(session_id, insertion)
            for sequence in leased_sequences:
                spool.ack(sequence)
            self.session_repository.finish(session_id, status="recovered")
            updated = self.session_repository.get(session_id)
            self.logger.info("session_recovery_finished session_id=%s", session_id)
            return (
                updated.committed_text if updated is not None and persists else record.committed_text + insertion
            )
        except Exception:
            for sequence in leased_sequences:
                try:
                    spool.release(sequence)
                except (OSError, SpoolError):
                    self.logger.warning("recovery_spool_release_failed sequence=%d", sequence)
            raise
        finally:
            spool.close()
            if spool.count == 0:
                try:
                    spool_directory.rmdir()
                except OSError:
                    self.logger.warning("recovered_spool_directory_not_removed")

    def _merge_recovered_segment(
        self,
        segment: AudioSegment,
        language: Language,
        previous_text: str,
        merger: TranscriptMerger,
    ) -> None:
        prompt = previous_text + merger.committed_text
        request = TranscriptionRequest(
            audio=segment.samples,
            sample_rate=segment.sample_rate,
            language=language.value,
            start_offset_seconds=segment.start_seconds,
            initial_prompt=bounded_prompt(prompt, self.vocabulary.entries),
            condition_on_previous_text=False,
            metadata={"segment_sequence": str(segment.sequence), "recovery": "true"},
        )
        if self._shutdown_requested.is_set():
            raise RuntimeError("shutdown requested during session recovery")
        guarded = self._transcribe_guarded_with_timeout(request, prompt)
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
        merger.merge(
            fragment,
            add_space=self.settings.dictation.add_fragment_space,
            capitalize=self.settings.dictation.capitalize_sentences,
        )

    def toggle_pause(self) -> bool:
        boundary = time.time()
        with self._lock:
            if self.state not in {
                AppState.LISTENING,
                AppState.SPEECH_DETECTED,
                AppState.TRANSCRIBING,
                AppState.PAUSED,
                AppState.PAUSED_TARGET_LOST,
            }:
                return False
            capture = self._capture
            audio_queue = self._audio_queue
            if not self._user_paused:
                self._user_paused = True
                self._pause_intervals.append((boundary, None))
                self._status_message = "Пауза"
                if self.state is not AppState.PAUSED:
                    self.state_machine.transition(AppState.PAUSED, "user paused capture")
            else:
                if audio_queue is None:
                    return False
                self._user_paused = False
                if self._pause_intervals and self._pause_intervals[-1][1] is None:
                    started, _ = self._pause_intervals[-1]
                    self._pause_intervals[-1] = (started, boundary)
                decision = self.target_manager.resolve()
                target_ready = decision.status.value == "ready"
                target_state = AppState.LISTENING if target_ready else AppState.PAUSED_TARGET_LOST
                self._status_message = (
                    "Возобновляю микрофон"
                    if target_ready
                    else self._target_warning(decision.status)
                )
                self.state_machine.transition_if(
                    {AppState.PAUSED}, target_state, "user resumed capture"
                )
                previous_pause = self._pause_thread
                resumer = threading.Thread(
                    target=self._resume_capture,
                    args=(audio_queue, previous_pause),
                    name="voxtype-capture-resumer",
                    daemon=True,
                )
                self._pause_thread = resumer
                resumer.start()
                return True
        if capture is not None:
            pauser = threading.Thread(
                target=capture.stop,
                kwargs={"drain_timeout": 5.0},
                name="voxtype-capture-pauser",
                daemon=True,
            )
            with self._lock:
                self._pause_thread = pauser
            pauser.start()
        return True

    def _captured_during_pause(self, captured_at: float) -> bool:
        """Reject callback chunks captured after Pause and before Resume."""

        with self._lock:
            return any(
                captured_at >= started and (ended is None or captured_at < ended)
                for started, ended in self._pause_intervals
            )

    def _resume_capture(
        self,
        audio_queue: LosslessAudioQueue,
        previous_pause: threading.Thread | None,
    ) -> None:
        if previous_pause is not None and previous_pause is not threading.current_thread():
            previous_pause.join(timeout=6.0)
            if previous_pause.is_alive():
                self._handle_runtime_error(
                    "Не удалось безопасно возобновить микрофон",
                    TimeoutError("previous audio capture did not stop before resume"),
                )
                return
        capture = self.capture_factory(audio_queue)
        with self._lock:
            if (
                self._shutdown_requested.is_set()
                or self._user_paused
                or self.state
                not in {
                    AppState.LISTENING,
                    AppState.SPEECH_DETECTED,
                    AppState.TRANSCRIBING,
                    AppState.PAUSED_TARGET_LOST,
                }
            ):
                return
            self._capture = capture
        try:
            capture.start()
        except Exception as exc:
            self._handle_runtime_error("Не удалось возобновить микрофон", exc)
            return
        with self._lock:
            cancelled = (
                self._shutdown_requested.is_set()
                or self._user_paused
                or self.state
                not in {
                    AppState.LISTENING,
                    AppState.SPEECH_DETECTED,
                    AppState.TRANSCRIBING,
                    AppState.PAUSED_TARGET_LOST,
                }
            )
            if not cancelled and self.state is not AppState.PAUSED_TARGET_LOST:
                self._status_message = "Слушаю"
        if cancelled:
            capture.stop(drain_timeout=2.0)

    def set_recording_availability(
        self, *, microphone: bool | None = None, model: bool | None = None
    ) -> None:
        with self._lock:
            if microphone is not None:
                self._microphone_available = microphone
            if model is not None:
                self._model_available = model

    def _readiness(self) -> AppReadiness:
        state = self.state
        busy = (
            self._recovery_active
            or self._file_recognition_active
            or self._inference_thread is not None
            or self._delivery_busy
            or self._session_id is not None
            or any(
                worker is not None and worker.is_alive()
                for worker in (self._start_thread, self._stop_thread, self._finish_thread)
            )
        )
        phase, reason, message = AppPhase.READY, "ready", "Готово"
        if self._shutdown_requested.is_set() or state is AppState.SHUTTING_DOWN:
            phase, reason, message = AppPhase.SHUTTING_DOWN, "shutdown", "Завершение работы"
        elif not self._initialized or state is AppState.STARTING:
            phase, reason, message = AppPhase.INITIALIZING, "initializing", "Подготовка"
        elif state is AppState.MODEL_LOADING:
            phase, reason, message = AppPhase.MODEL_LOADING, "model_loading", "Загрузка модели"
        elif state is AppState.FINISHING:
            phase, reason, message = AppPhase.PROCESSING, "processing", "Обработка записи"
        elif self._file_recognition_active:
            phase, reason, message = AppPhase.PROCESSING, "file_processing", "Распознавание аудиофайла"
        elif state in {
            AppState.LISTENING, AppState.SPEECH_DETECTED, AppState.TRANSCRIBING,
            AppState.PAUSED, AppState.PAUSED_TARGET_LOST,
        }:
            phase, reason, message = AppPhase.RECORDING, "recording", "Запись"
        elif self._delivery_busy:
            phase, reason, message = AppPhase.INSERTING, "inserting", "Отправка текста"
        elif not self._model_available:
            phase, reason, message = AppPhase.MODEL_REQUIRED, "model_missing", "Требуется модель"
        elif not self._microphone_available:
            phase, reason, message = (
                AppPhase.MICROPHONE_UNAVAILABLE, "microphone_missing", "Микрофон недоступен"
            )
        elif state in {AppState.ERROR_RECOVERABLE, AppState.ERROR_FATAL}:
            phase, reason, message = AppPhase.ERROR, "operation_failed", "Требуется внимание"
        elif busy:
            phase, reason, message = AppPhase.PROCESSING, "worker_busy", "Завершение операции"
        return AppReadiness(
            phase, reason, message,
            not busy and phase in {AppPhase.READY, AppPhase.ERROR}
            and state in {AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE},
            state is AppState.FINISHING and self._session_id is not None
            and not self._processing_cancelled.is_set(),
        )

    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            capture = self._capture
            stats = capture.stats() if capture is not None else self._last_stats
            started = self._session_started_monotonic
            stopped = self._session_stopped_monotonic
            elapsed = (
                (stopped if stopped is not None else time.monotonic()) - started
                if started is not None
                else self._completed_duration_s
            )
            if (
                getattr(self.target_manager, "policy", TargetPolicy.LOCKED)
                is TargetPolicy.FOLLOW_FOCUS
            ):
                try:
                    target = self.target_manager.resolve().target
                except (OSError, RuntimeError):
                    target = None
            else:
                target = self.target_manager.locked_target
            current_language = self._worker_language if self._session_id is not None else self._language
            current_model = (self._session_backend or self.backend).model_id
            return ControllerSnapshot(
                state=self.state.name,
                language=current_language,
                session_elapsed_s=max(0.0, elapsed),
                queued_audio_seconds=stats.queued_audio_seconds if stats is not None else 0.0,
                last_committed_text=self._last_fragment,
                provisional_text=self._provisional,
                target_title=target.title if target is not None else "",
                input_rms=stats.current_input_rms if stats is not None else 0.0,
                clipping_percentage=stats.clipping_percentage if stats is not None else 0.0,
                warning=self._status_message,
                microphone_name=self.microphone_name,
                model_name=current_model,
                model_profile=self.settings.dictation.model_profile,
                status_message=self._status_message,
                error_message=self._error_message,
                full_transcript=self.merger.committed_text or self._completed_transcript,
                delivery_status=self._delivery_status,
                readiness=self._readiness(),
            )

    def sample_resources(self) -> None:
        """Capture one bounded in-memory resource sample for diagnostics."""

        with self._lock:
            capture = self._capture
            stats = capture.stats() if capture is not None else self._last_stats
        queued = stats.queued_audio_seconds if stats is not None else 0.0
        spool_bytes = stats.spooled_bytes if stats is not None else 0
        if stats is not None:
            self.metrics.sync_audio(
                captured_frames=stats.captured_frames,
                dropped_frames=stats.dropped_frames,
                queued_audio_seconds=stats.queued_audio_seconds,
                processed_audio_seconds=stats.processed_audio_seconds,
                queue_high_watermark=stats.queue_high_watermark,
                callback_overflows=stats.callback_overflows,
                current_input_rms=stats.current_input_rms,
                clipping_percentage=stats.clipping_percentage,
            )
        self.metrics.sample_resources(round(queued / 0.03), spool_bytes)

    def _handle_runtime_error(self, user_message: str, error: BaseException) -> None:
        self.logger.exception("runtime_error kind=%s", type(error).__name__, exc_info=error)
        if isinstance(error, ASRModelUnavailable):
            user_message = (
                "Локальная модель не установлена. Откройте настройки и установите модель."
            )
        elif isinstance(error, AudioIngressOverflowError):
            user_message = (
                "Микрофон потерял часть аудио. Проверьте устройство записи и повторите диктовку."
            )
        elif isinstance(error, InferenceTimeoutError):
            user_message = (
                "Модель не ответила вовремя. Текст и оставшееся аудио сохранены для восстановления."
            )
        elif isinstance(error, ProcessingCancelled):
            user_message = (
                "Обработка отменена. Текст и оставшееся аудио сохранены для восстановления."
            )
        with self._lock:
            self._error_message = user_message
            self._status_message = (
                "Накопленный текст сохранён в журнале"
                if self._journal is not None and self._journal.persist_text
                else "Накопленный текст доступен в результате"
            )
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
        target_state = AppState.ERROR_RECOVERABLE
        if self.state not in {
            AppState.ERROR_RECOVERABLE,
            AppState.ERROR_FATAL,
            AppState.SHUTTING_DOWN,
        }:
            try:
                self.state_machine.transition(target_state, "runtime component failed")
            except Exception:
                self.logger.exception("error_state_transition_failed")
        if session_id is not None:
            self._finalize_session("recovery_required", requires_recovery=True)

    def handle_ui_exception(self, error: BaseException) -> None:
        """Convert an uncaught Qt callback exception into a recoverable state."""

        self._handle_runtime_error("Ошибка интерфейса; накопленный текст сохранён", error)

    def _complete_session(self, status: str) -> None:
        # A completed recording is a durable user-visible state. Target loss must
        # not keep the UI in FINISHING for 30 seconds: the FIFO remains available
        # for explicit retry/copy from the completed-result panel.
        self._finalize_session(status)

    def _drain_pending_injection(self) -> None:
        deadline = time.monotonic() + 30.0
        while (
            int(getattr(self.injection_service, "pending_fragments", 0)) > 0
            and time.monotonic() < deadline
            and not self._shutdown_requested.wait(0.25)
        ):
            result = self.injection_service.flush()
            if result.status is InjectionStatus.FAILED:
                with self._lock:
                    self._status_message = "Вставка не удалась; текст доступен в истории"
            elif result.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                with self._lock:
                    self._status_message = "Целевое окно неактивно; ожидаю возврата"
        if int(getattr(self.injection_service, "pending_fragments", 0)) == 0:
            self._finalize_session("completed")
        elif not self._shutdown_requested.is_set():
            with self._lock:
                self._error_message = (
                    "Автовставка недоступна. Накопленный текст сохранён — "
                    "используйте «Копировать текст»."
                )
                self._status_message = "Ожидаю безопасного копирования текста"

    def flush_pending_text(self) -> bool:
        result = self.injection_service.flush()
        inserted = result.status is InjectionStatus.INSERTED
        if inserted and not self._user_paused:
            with self._lock:
                self._error_message = ""
                self._status_message = "Слушаю"
            self.state_machine.transition_if(
                {AppState.PAUSED_TARGET_LOST},
                AppState.LISTENING,
                "pending text delivered to returned target",
            )
        return inserted

    def professional_session_is_current(self, number: int) -> bool:
        with self._lock:
            return (
                number == self._professional_session_number
                and not self._shutdown_requested.is_set()
                and not self._processing_cancelled.is_set()
            )

    def cancel_professional_session(self, number: int) -> bool:
        if not self.professional_session_is_current(number):
            return False
        self.stop_dictation()
        return self.cancel_processing()

    def insert_professional_text(self, text: str, number: int | None = None) -> bool:
        if not text:
            return False
        with self._lock:
            if (
                self._shutdown_requested.is_set() or self._delivery_busy
                or (number is not None and not self.professional_session_is_current(number))
                or (number is None and self.state not in {AppState.IDLE, AppState.COMPLETED})
            ):
                return False
            if number is None:
                decision = self.target_manager.start_session()
                target = getattr(decision, "target", None)
                if target is None or self.policy.blocked(DictationContext.capture(target, "locked")):
                    return False
            journal = self._journal if number is not None and self._session_id is not None else None
            self._delivery_busy = True
        try:
            self.target_manager.try_restore_focus()
            if journal is not None:
                journal.commit_text(text)
            if isinstance(self.injection_service, TextInjectionService):
                result = self.injection_service.submit(
                    text, exact_text=True, strategy=self._session_profile.insertion_mode or None,
                )
            else:
                result = self.injection_service.submit(text)
            with self._lock:
                self._delivery_status = {
                    InjectionStatus.INSERTED: "delivered",
                    InjectionStatus.COPIED: "copied",
                    InjectionStatus.QUEUED: "queued_target_inactive",
                }.get(result.status, "copy_required")
            return result.status in {InjectionStatus.INSERTED, InjectionStatus.COPIED, InjectionStatus.QUEUED}
        except Exception as exc:
            self.logger.warning("professional_insertion_failed type=%s", type(exc).__name__)
            with self._lock:
                self._error_message = "Не удалось отправить текст в целевое окно"
            return False
        finally:
            with self._lock:
                self._delivery_busy = False

    def read_professional_selection(self) -> SelectedText:
        with self._lock:
            if self._delivery_busy or self._shutdown_requested.is_set() or self.state not in {
                AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE,
            }:
                raise SelectionUnavailable("Сначала завершите активную диктовку")
            self._delivery_busy = True
        try:
            decision = self.target_manager.start_session()
            if decision.target is None or self.policy.blocked(
                DictationContext.capture(decision.target, "selection")
            ) or not (
                decision.can_inject or self.target_manager.try_restore_focus()
            ):
                raise SelectionUnavailable("Не удалось вернуть фокус в исходный редактор")
            return SelectionProvider.read(decision.target)
        finally:
            with self._lock:
                self._delivery_busy = False

    def replace_professional_selection(self, selection: SelectedText, text: str) -> bool:
        with self._lock:
            if self._delivery_busy or self._shutdown_requested.is_set() or self.state not in {
                AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE,
            }:
                return False
            self._delivery_busy = True
        try:
            if self.policy.blocked(DictationContext.capture(selection.target, "selection")):
                return False
            self.target_manager.set_locked_target(selection.target)
            if not self.target_manager.try_restore_focus():
                return False
            try:
                current = SelectionProvider.read(selection.target)
            except SelectionUnavailable:
                return False
            return (
                current.text == selection.text
                and current.control_id == selection.control_id
                and self.injection_service.dispatch_explicit(text, selection.target)
            )
        finally:
            with self._lock:
                self._delivery_busy = False

    def copy_pending_text(self) -> str:
        result = self.injection_service.copy_pending_to_clipboard(consume=True)
        return result.delivered_text

    def copy_previous_result(self) -> bool:
        with self._lock:
            text = self._previous_completed_text
        return bool(text and self.injection_service.copy_text_to_clipboard(text))

    def copy_full_transcript(self) -> bool:
        """Copy the complete durable result; intended for a background UI worker."""

        with self._lock:
            text = self.merger.committed_text or self._completed_transcript
        copied = self.injection_service.copy_text_to_clipboard(text)
        with self._lock:
            if copied:
                self._status_message = "Весь текст скопирован"
            else:
                self._error_message = "Не удалось скопировать текст"
        return copied

    def retry_completed_insertion(self) -> bool:
        """Reserve the completed session while dispatching its pending text."""
        with self._lock:
            if (
                self.state is not AppState.COMPLETED
                or self._delivery_busy
                or self._shutdown_requested.is_set()
                or not self.injection_service.pending_fragments
            ):
                return False
            self._delivery_busy = True
        try:
            return self._retry_retained_insertion()
        finally:
            with self._lock:
                self._delivery_busy = False

    def _retry_retained_insertion(self) -> bool:
        result = self.injection_service.flush()
        with self._lock:
            if result.status is InjectionStatus.INSERTED:
                self._delivery_status = "delivered"
                self._status_message = "Команда вставки отправлена"
                self._error_message = ""
                if int(getattr(self.injection_service, "pending_fragments", 0)) == 0:
                    self.target_manager.end_session()
                return True
            if result.status is InjectionStatus.COPIED:
                self._delivery_status = "copied"
                self._status_message = "Текст скопирован"
                self._error_message = ""
                return False
            temporarily_unavailable = (
                result.status is InjectionStatus.QUEUED
                and result.target_status in {TargetStatus.TARGET_INACTIVE, TargetStatus.SUSPENDED}
            ) or (
                result.status is InjectionStatus.BLOCKED
                and result.target_status is TargetStatus.READY
            )
            if temporarily_unavailable:
                self._delivery_status = "queued_target_inactive"
                self._status_message = "Целевое окно неактивно; текст сохранён"
            elif result.status in {InjectionStatus.BLOCKED, InjectionStatus.QUEUED}:
                self._delivery_status = "copy_required"
                self._error_message = "Безопасная вставка недоступна. Скопируйте весь текст."
            else:
                self._delivery_status = "failed"
                self._error_message = "Вставка не удалась; весь текст можно скопировать"
        return False

    def _finalize_session(self, status: str, *, requires_recovery: bool = False) -> None:
        with self._lock:
            session_id = self._session_id
            if session_id is None:
                if self.state is AppState.FINISHING:
                    self._status_message = "Остановлено"
                    self.state_machine.transition(AppState.COMPLETED, "empty session stopped")
                return
            # Claim finalization before I/O so safe-copy and foreground polling
            # cannot finish the same SQLite session concurrently.
            self._session_id = None
            capture = self._capture
            spool = self._spool
            audio_archive = self._audio_archive
            self._audio_archive = None
            stats = capture.stats() if capture is not None else self._last_stats
            completed_text = self.merger.committed_text
            started = self._session_started_monotonic
            stopped = self._session_stopped_monotonic or time.monotonic()
        if session_id is not None:
            if stats is not None:
                self.session_repository.update_metrics(
                    session_id,
                    audio_duration_s=stats.processed_audio_seconds,
                    processing_s=self._processing_seconds,
                    dropped_frames=stats.dropped_frames,
                )
            if requires_recovery:
                self.session_repository.require_recovery(session_id)
            else:
                self.session_repository.finish(session_id, status=status)
            try:
                self.usage_metrics.record(
                    self._session_profile.id, status,
                    stats.processed_audio_seconds if stats is not None else 0.0,
                    len(completed_text.split()), self._processing_seconds,
                )
            except Exception as exc:
                self.logger.warning("usage_metrics_write_failed type=%s", type(exc).__name__)
            if (
                not requires_recovery
                and status.startswith("completed")
                and (not self.settings.privacy.save_history or self.policy.disable_history)
            ):
                self.session_repository.delete(session_id)
        # Retain the original window identity while committed fragments still
        # await delivery. Otherwise a completed session can never flush when
        # the user returns to that window.
        if int(getattr(self.injection_service, "pending_fragments", 0)) == 0:
            self.target_manager.end_session()
        if audio_archive is not None:
            audio_archive.close()
        no_result = status == "completed" and not completed_text.strip()
        capture_empty = stats is not None and stats.processed_audio_seconds == 0
        if no_result:
            self.logger.info(
                "dictation_no_result captured_frames=%s processed_seconds=%s",
                stats.captured_frames if stats is not None else None,
                stats.processed_audio_seconds if stats is not None else None,
            )
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
            self._session_backend = None
            self._audio_queue = None
            self._spool = None
            self._segmenter = None
            self._journal = None
            self._completed_transcript = completed_text
            if completed_text.strip() and status == "completed":
                self._previous_completed_text = completed_text
            self._completed_duration_s = max(0.0, stopped - started) if started is not None else 0.0
            self._session_started_monotonic = None
            self._session_stopped_monotonic = None
            self._capture_started_monotonic = None
            self._pipeline_thread = None
            self._pending_delivery_thread = None
            self._status_message = "Готово" if status == "completed" else self._status_message
            if no_result:
                self._delivery_status = "no_result"
                self._status_message = (
                    "Звук не поступил. Проверьте выбранный микрофон и разрешения Windows."
                    if capture_empty
                    else "Речь не распознана. Проверьте уровень микрофона и повторите диктовку."
                )
            elif not self.settings.dictation.auto_insert:
                self._delivery_status = "pending"
            elif self._delivery_status not in {"failed", "copied"}:
                self._delivery_status = (
                    "delivered"
                    if int(getattr(self.injection_service, "pending_fragments", 0)) == 0
                    else "queued_target_inactive"
                )
            self._user_paused = False
        if status == "completed" and completed_text.strip():
            self.workflows.dispatch("dictation", self._session_profile.id, completed_text)
        if self.state is AppState.FINISHING:
            self.state_machine.transition(AppState.COMPLETED, "session drained and committed")
            if self.settings.dictation.copy_full_text_on_complete and completed_text:
                copier = getattr(self.injection_service, "copy_text_to_clipboard", None)
                copied = bool(copier(completed_text)) if callable(copier) else False
                with self._lock:
                    if copied:
                        self._status_message = "Готово — весь текст скопирован"
                    else:
                        self._error_message = "Текст сохранён, но буфер обмена недоступен"

    def shutdown(self) -> bool:
        self._shutdown_requested.set()
        with self._lock:
            if self._file_recognition_active:
                self._file_recognition_cancel.set()
                return False
            if self._recovery_active:
                self.logger.info("shutdown_deferred recovery_still_running=true")
                return False
        if self.state in {AppState.STARTING, AppState.MODEL_LOADING}:
            self._start_cancelled.set()
        if self.state is AppState.MODEL_LOADING and self._session_id is not None:
            with self._lock:
                audio_queue = self._audio_queue
            if audio_queue is not None:
                audio_queue.close()
            self._finalize_session("interrupted")
        if self.state in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
            AppState.PAUSED,
            AppState.PAUSED_TARGET_LOST,
            AppState.STARTING,
            AppState.MODEL_LOADING,
        }:
            self.stop_dictation()
        with self._lock:
            starter = self._start_thread
            preloader = self._preload_thread
            stopper = self._stop_thread
        for worker in (starter, preloader):
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=30.0)
                if worker.is_alive():
                    self.logger.error("shutdown_deferred worker_still_running=%s", worker.name)
                    return False
        if stopper is not None and stopper is not threading.current_thread():
            stopper.join(timeout=12.0)
            if stopper.is_alive():
                self.logger.error("shutdown_deferred worker_still_running=%s", stopper.name)
                return False
        with self._lock:
            finisher = self._finish_thread
        if finisher is not None and finisher is not threading.current_thread():
            finisher.join(timeout=30.0)
            if finisher.is_alive():
                self.logger.error("shutdown_deferred worker_still_running=%s", finisher.name)
                return False
        pipeline_alive = False
        with self._lock:
            pipeline_alive = self._pipeline_thread is not None and self._pipeline_thread.is_alive()
            if self._inference_thread is not None:
                self.logger.info("shutdown_deferred native_inference_still_running=true")
                return False
        if self.state is not AppState.SHUTTING_DOWN:
            if self.state is AppState.FINISHING:
                self._status_message = "Завершение продолжается в журнале"
            try:
                self.state_machine.transition(AppState.SHUTTING_DOWN, "application shutdown")
            except Exception:
                self.logger.exception("shutdown_transition_failed")
        if pipeline_alive:
            self.logger.error("shutdown_deferred pipeline_still_running=true")
            return False
        try:
            self._delivery_queue.put(None, timeout=1.0)
        except Full:
            self.logger.error("shutdown_deferred delivery_queue_full=true")
            return False
        if self._delivery_thread is not threading.current_thread():
            self._delivery_thread.join(timeout=2.0)
        if self._delivery_thread.is_alive():
            self.logger.error("shutdown_deferred delivery_worker_alive=true")
            return False
        self.injection_service.close()
        if self._file_backend is not None:
            self._file_backend.unload()
            self._file_backend = None
        return True

    @staticmethod
    def _target_warning(status: object) -> str:
        value = getattr(status, "value", str(status or ""))
        messages = {
            "higher_integrity": "Windows блокирует вставку в окно с повышенными правами",
            "integrity_unknown": "Безопасность целевого окна не удалось проверить",
            "target_closed": "Целевое окно закрыто",
            "target_reused": "Целевое окно было заменено другим процессом",
            "own_window": "VoxType не вставляет текст в собственное окно",
            "no_target": "Целевое окно не выбрано",
            "suspended": "Сеанс Windows временно приостановлен",
            "enterprise_blocked": "Диктовка в этом приложении запрещена политикой организации",
        }
        return messages.get(
            str(value),
            "Щёлкните поле целевого окна для вставки; текст сохранён",
        )

    def _require_session_id(self) -> str:
        with self._lock:
            if self._session_id is None:
                raise RuntimeError("no active session")
            return self._session_id
    def workflow_input(self) -> tuple[str, str]:
        with self._lock:
            return self._session_profile.id, self._completed_transcript

