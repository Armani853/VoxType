"""Lifecycle controller joining capture, segmentation, ASR, journal and injection."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Full, Queue

import numpy as np

from voxtype.asr.base import (
    ASRBackend,
    ASRModelUnavailable,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    bounded_prompt,
)
from voxtype.asr.hallucination_guard import GuardedTranscription, HallucinationGuard
from voxtype.asr.live import LiveSpeechSession, LiveTranscript, SpeechKind, SpeechSegment
from voxtype.audio.capture import AudioCaptureService, AudioIngressOverflowError
from voxtype.audio.models import AudioQueueStats
from voxtype.audio.quality import AudioQualityMonitor, AudioQualitySnapshot
from voxtype.audio.spool import AudioQueueLease, DiskSpool, LosslessAudioQueue, SpoolError
from voxtype.core.compute_budget import ResourcePhase
from voxtype.core.feedback import FAILURES, FailureCode
from voxtype.core.gestures import DictationMode, SilenceAutoStopSettings
from voxtype.core.models import (
    AppPhase,
    AppReadiness,
    ControllerSnapshot,
    Language,
    ModelProfile,
    RecognitionResult,
    RecognitionStatus,
    TranscriptFragment,
    WordStamp,
)
from voxtype.core.profiles import (
    BUILTIN_PROFILES,
    DictationContext,
    DictationProfile,
    OutputLanguagePolicy,
    ProfileManager,
)
from voxtype.core.state_machine import AppState, StateMachine
from voxtype.monitoring.metrics import PerformanceMonitor
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.repository import SessionRepository
from voxtype.sessions.usage import UsageMetricsService
from voxtype.settings.enterprise import EnterprisePolicyService
from voxtype.settings.organization_pack import OrganizationPackService
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings
from voxtype.transcript.formatter import format_fragment_boundary
from voxtype.transcript.merger import TranscriptMerger, deduplicate_text_boundary
from voxtype.transcript.professional import (
    CommandResolution,
    ProcessedDictation,
    ProfessionalContent,
    TextPipeline,
    VoiceAction,
)
from voxtype.transcript.structured import FormattingState, StructuredDictationFormatter
from voxtype.transcript.vocabulary import Vocabulary
from voxtype.transcript.workflows import WorkflowService
from voxtype.vad.segmenter import AudioSegment, StreamingSegmenter
from voxtype.windows.adaptive_input import InputTargetClassifier, InputTargetKind
from voxtype.windows.foreground import (
    WindowMetadata,
    focused_control_type,
    get_foreground_hwnd,
    get_window_process_id,
)
from voxtype.windows.injection import DeliveryStatus, InjectionStatus, TextInjectionService
from voxtype.windows.input_context import ForegroundContext, ForegroundContextProvider
from voxtype.windows.selection import SelectedText, SelectionProvider, SelectionUnavailable
from voxtype.windows.target_window import TargetStatus, TargetWindowManager

CaptureFactory = Callable[[LosslessAudioQueue], AudioCaptureService]
SegmenterFactory = Callable[[], StreamingSegmenter]


class InferenceTimeoutError(RuntimeError):
    """A native inference call exceeded the bounded session deadline."""


class ProcessingCancelled(RuntimeError):
    """The user stopped processing pending audio."""


@dataclass(frozen=True, slots=True)
class _LivePreviewJob:
    request: TranscriptionRequest
    started: float
    results: Queue[TranscriptionResult | BaseException]


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
        enterprise: EnterprisePolicyService | None = None,
    ) -> None:
        if inference_timeout_s <= 0:
            raise ValueError("inference_timeout_s must be positive")
        self.settings_repository = settings_repository
        self.session_repository = session_repository
        self.backend = backend
        self._loaded_backend: ASRBackend | None = None
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
        self._model_switch_active = False
        loaded_settings = self.settings_repository.load()
        self.usage_metrics = UsageMetricsService(
            session_repository,
            settings_repository,
            loaded_settings.professional.get("typing_speed_wpm"),
        )
        self.profiles = ProfileManager(settings_repository, loaded_settings.professional)
        self.enterprise = enterprise or EnterprisePolicyService()
        self.policy = self.enterprise.policy
        self.professional_content = ProfessionalContent(
            settings_repository,
            loaded_settings.professional,
            allow_organization_vocabulary=self.policy.allow_organization_vocabulary,
        )
        self.organization_packs = OrganizationPackService(
            settings_repository,
            self.profiles,
            self.professional_content,
        )
        if (
            self.policy.disable_history or not self.policy.allow_clipboard_persistence
        ) and isinstance(injection_service, TextInjectionService):
            injection_service.configure_pending_storage(False)
        if self.policy.disabled_applications:
            self.target_manager.application_blocked = lambda window: self.policy.blocked(
                DictationContext.capture(window, "locked")
            )
        self.workflows = WorkflowService(
            settings_repository, session_repository, loaded_settings.professional
        )
        self.professional_action: (
            Callable[[int, VoiceAction, DictationProfile, CommandResolution | None], None] | None
        ) = None
        self.profile_backend_factory: Callable[[str], ASRBackend] | None = None
        self._session_profile = self.profiles.resolve(DictationContext())
        self._session_context = DictationContext()
        self._session_backend: ASRBackend | None = None
        self._input_context: ForegroundContext | None = None
        self._terminal_input = False
        self._session_mode = DictationMode.TOGGLE
        self._session_auto_stop = SilenceAutoStopSettings()
        self._auto_stop_timer: threading.Timer | None = None
        self._audio_quality: AudioQualitySnapshot | None = None
        self._audio_monitor_active = False
        self._live_session: LiveSpeechSession | None = None
        self._live_command_text: str | None = ""
        self._live_command_resolution: CommandResolution | None = None
        self._live_command_claimed = False
        self._live_text_committed = False
        self._live_transcript: LiveTranscript | None = None
        self._live_final: SpeechSegment | None = None
        self._audio_review_required = False
        self._system_unavailable = False
        self._session_interrupted = False
        self._target_reselect_required = False
        self._professional_session_number = 0
        self._session_text_pipeline = self.professional_content.snapshot()
        self._command_mode = False
        self._formatting_state = FormattingState()
        self._last_processed: ProcessedDictation | None = None
        self._recognition_result: RecognitionResult | None = None
        self._raw_asr_parts: list[str] = []
        self._recognized_segments: list[TranscriptionSegment] = []
        self._practice_language: Language | None = None
        self._local_only = False
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
        self._selection_provider = SelectionProvider()
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
        self._shutdown_complete = False
        self._delivery_stop_sent = False
        self._start_cancelled = threading.Event()
        self._processing_cancelled = threading.Event()
        self._delivery_busy = False
        self._outbox_session_id: str | None = None
        self._delivery_queue: Queue[tuple[str, str, bool] | None] = Queue(maxsize=256)
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
            self._loaded_backend = self.backend
            if self.state is AppState.MODEL_LOADING:
                self.state_machine.transition(AppState.IDLE, "model preloaded")

    def toggle_dictation(self) -> None:
        current = self.state
        if current is AppState.ERROR_RECOVERABLE:
            self.restart_dictation()
        elif current in {AppState.IDLE, AppState.COMPLETED}:
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

    @property
    def _pending_insertion_count(self) -> int:
        return int(getattr(self.injection_service, "pending_fragments", 0))

    def start_dictation(
        self,
        *,
        practice_language: Language | None = None,
        practice_capture: CaptureFactory | None = None,
        local_only: bool = False,
    ) -> bool:
        with self._lock:
            readiness = self._readiness()
            if self._system_unavailable or (
                self._pending_insertion_count
                and (
                    self._target_reselect_required
                    or (
                        isinstance(self.injection_service, TextInjectionService)
                        and self.injection_service.context_provider is not None
                    )
                )
            ):
                self._error_message = "Сначала скопируйте или отправьте ожидающий текст."
                return False
            if not readiness.can_record and not (
                practice_capture is not None
                and practice_language is not None
                and readiness.phase is AppPhase.MICROPHONE_UNAVAILABLE
                and self.can_change_runtime_settings()
            ):
                return False
            self._start_cancelled.clear()
            self._processing_cancelled.clear()
            self._live_session = None
            self._live_transcript = None
            self._live_final = None
            self._live_command_text = ""
            self._live_command_resolution = None
            self._live_command_claimed = False
            self._live_text_committed = False
            self._audio_review_required = False
            self._audio_quality = None
            self._session_interrupted = False
            self._target_reselect_required = False
            decision = self.target_manager.start_session()
            target = getattr(decision, "target", None)
            context = DictationContext.capture(target, self.settings.dictation.target_mode.value)
            self._terminal_input = False
            self._input_context = (
                ForegroundContextProvider.native(target, context.selection_mode)
                if isinstance(target, WindowMetadata)
                else None
            )
            if isinstance(target, WindowMetadata):
                context = replace(
                    context,
                    control_type=self._input_context.control_type
                    if self._input_context
                    else focused_control_type(target),
                )
            if self.policy.blocked(context) or (
                self.policy.disabled_applications
                and self.policy.blocked(
                    DictationContext.capture(getattr(decision, "foreground", None), "locked")
                )
            ):
                self._error_message = "Диктовка в этом приложении запрещена политикой организации"
                self._status_message = self._error_message
                self.target_manager.end_session()
                return False
            self._session_mode = self.settings.dictation.mode
            self._session_auto_stop = self.settings.dictation.auto_stop
            self._session_profile = self.profiles.resolve(context)
            if not self.profile_allowed(self._session_profile):
                self._error_message = (
                    "Выбранный профиль или его модель запрещены политикой организации"
                )
                self._status_message = self._error_message
                self.target_manager.end_session()
                return False
            if isinstance(
                self.injection_service, TextInjectionService
            ) and not self.injection_service.configure_pending_storage(
                self.settings.privacy.save_history
                and not self.policy.disable_history
                and self.policy.allow_clipboard_persistence
            ):
                self._error_message = "Скопируйте ожидающий доставки текст перед началом записи"
                self.target_manager.end_session()
                return False
            self._session_context = replace(context, profile_id=self._session_profile.id)
            self._practice_language = practice_language
            self._local_only = local_only
            self._worker_language = practice_language or (
                Language(self._session_profile.language)
                if self._session_profile.language
                else self._language
            )
            self._professional_session_number += 1
            self._session_text_pipeline = self.professional_content.snapshot()
            if not self.settings.dictation.user_text_transformations:
                self._session_text_pipeline = replace(
                    self._session_text_pipeline, dictionary=(), replacements=()
                )
            if not self.policy.allow_user_snippets:
                self._session_text_pipeline = replace(self._session_text_pipeline, snippets=())
            self._command_mode = self.settings.dictation.voice_commands
            self._formatting_state = FormattingState()
            self._last_processed = None
            self._recognition_result = None
            self._raw_asr_parts.clear()
            self._recognized_segments.clear()
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
                args=(decision, practice_capture or self.capture_factory),
                name="voxtype-session-starter",
                daemon=True,
            )
            self._start_thread = starter
            starter.start()
        return True

    def _prepare_and_start_runtime(self, decision: object, capture_factory: CaptureFactory) -> None:
        """Build disk/audio/model runtime away from the Qt and hotkey threads."""

        try:
            target = getattr(decision, "target", None)
            target_name = target.process_name if target is not None else ""
            profile = self.settings.dictation.model_profile
            professional = self._session_profile
            service = self.injection_service
            if isinstance(service, TextInjectionService) and service.context_provider is not None:
                seed = self._input_context
                enriched = (
                    service.context_provider.capture(target, mode=seed.target_mode)
                    if seed is not None and isinstance(target, WindowMetadata)
                    else None
                )
                context = (
                    enriched
                    if enriched and seed and enriched.focused_hwnd == seed.focused_hwnd
                    else seed
                )
                rule = self.profiles.compatibility_rule(professional, self._session_context)
                kind = (
                    InputTargetClassifier.classify(context) if context else InputTargetKind.UNKNOWN
                )
                terminal = kind is InputTargetKind.TERMINAL
                if terminal or (rule and rule.formatting_policy == "preserve"):
                    professional = replace(professional, formatting_mode="verbatim")
                with self._lock:
                    self._input_context = context
                    self._terminal_input = terminal
                    self._session_profile = professional
                service.begin_adaptive_input(
                    context,
                    rule,
                    terminal_safe=self.settings.dictation.safe_terminal_input,
                )
            language = self._worker_language
            practice = self._practice_language is not None
            backend = self.backend
            if professional.model_id and professional.model_id != backend.model_id:
                alternate = self._file_backend
                if alternate is not None and alternate.model_id == professional.model_id:
                    backend = alternate
                elif self.profile_backend_factory is None:
                    raise ASRModelUnavailable("Profile model is unavailable")
                else:
                    backend = self.profile_backend_factory(professional.model_id)
                    previous_alternate = self._file_backend
                    self._file_backend = backend
                    if previous_alternate is not None:
                        previous_alternate.unload()
            session_id = self.session_repository.create(
                language,
                backend.model_id,
                profile,
                target_name,
                profile_id=professional.id,
            )
            journal = SessionJournal(
                self.session_repository,
                session_id,
                persist_text=self.settings.privacy.save_history
                and not self.policy.disable_history
                and not practice,
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
            if (
                self.settings.privacy.save_audio
                and not self.policy.disable_audio_storage
                and not practice
            ):
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
                None
                if self.policy.disable_audio_storage
                or practice
                or not self.settings.privacy.save_audio
                else DiskSpool(session_spool, max_bytes=4 * 1024**3)
            )
            if journal.persist_text or spool is not None:
                self.session_repository.mark_recoverable(session_id)
            audio_queue = LosslessAudioQueue(
                max_ram_chunks=4096 if spool is None else 128,
                spool=spool,
                spill_threshold=0.70,
                max_inflight=2048,
                durable=spool is not None,
                session_id=self._professional_session_number,
            )
            segmenter = self.segmenter_factory()
            capture = capture_factory(audio_queue)
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
                if self.settings.dictation.live_preview and not practice:
                    self._live_session = LiveSpeechSession(
                        session_id, language.value, backend.model_id, segmenter.sample_rate
                    )
            if self._start_cancelled.is_set() or self.state is AppState.FINISHING:
                audio_queue.close()
                self._finalize_session("cancelled")
                return
            self.state_machine.transition_if(
                {AppState.STARTING}, AppState.MODEL_LOADING, "session runtime prepared"
            )
            with self._lock:
                cancelled = self.state is not AppState.MODEL_LOADING
                if not cancelled:
                    self._status_message = "Подготовка модели"
            if cancelled:
                audio_queue.close()
                self._finalize_session("cancelled")
                return
            self._load_and_start_runtime()
        except Exception as exc:
            self._handle_runtime_error("Не удалось начать диктовку", exc)

    def _load_and_start_runtime(self) -> None:
        try:
            if self._start_cancelled.is_set():
                self._finalize_session("cancelled")
                return
            backend = self._session_backend or self.backend
            capture = self._capture
            warm = (
                isinstance(capture, AudioCaptureService)
                and self.settings.audio.startup_pre_roll
                and self._practice_language is None
                and self._spool is None
                and self._audio_archive is None
            )
            if warm:
                assert isinstance(capture, AudioCaptureService)
                capture.arm_pre_roll()
                capture.start(cancel=self._start_cancelled)
            if not self._start_cancelled.is_set():
                backend.load()
            with self._lock:
                cancelled = self._start_cancelled.is_set() or self._shutdown_requested.is_set()
                audio_queue = self._audio_queue
                capture = self._capture
                if not cancelled:
                    if audio_queue is None or capture is None:
                        raise RuntimeError("session runtime was disposed while loading")
                    if backend is self.backend:
                        self._loaded_backend = backend
            if cancelled or self._start_cancelled.is_set():
                if warm and capture is not None:
                    capture.stop(drain_timeout=2.0)
                if audio_queue is not None:
                    audio_queue.close()
                self._finalize_session("cancelled")
                return
            assert capture is not None and audio_queue is not None
            # PortAudio can block inside a driver. Never hold the controller lock
            # while opening the stream: the GUI needs that lock for snapshot().
            if not warm:
                capture.start()
            journal = self._journal
            if journal is not None and not self._start_cancelled.is_set():
                journal.append("capture_started", {"pre_roll": warm})
            with self._lock:
                cancelled = (
                    self._start_cancelled.is_set()
                    or self._shutdown_requested.is_set()
                    or self.state is not AppState.MODEL_LOADING
                )
                if not cancelled:
                    if isinstance(capture, AudioCaptureService) and warm:
                        capture.activate_session()
                    self._pipeline_thread = threading.Thread(
                        target=self._pipeline_loop,
                        name="voxtype-transcription",
                        daemon=True,
                    )
                    self._pipeline_thread.start()
                    self._capture_started_monotonic = time.monotonic()
                    self._status_message = "Слушаю"
                    self.state_machine.transition(AppState.IDLE, "model ready for session")
                    self.state_machine.transition(AppState.LISTENING, "microphone capture started")
                    if (
                        self._session_mode == DictationMode.AUTO_STOP
                        and self._session_auto_stop.enabled
                        and self._practice_language is None
                    ):
                        timer = threading.Timer(
                            self._session_auto_stop.duration_limit_ms / 1000,
                            self._stop_at_duration_limit,
                            args=(self._professional_session_number,),
                        )
                        timer.daemon = True
                        self._auto_stop_timer = timer
                        timer.start()
            if cancelled:
                capture.stop(drain_timeout=2.0)
                audio_queue.close()
                self._finalize_session("cancelled")
        except Exception as exc:
            self._handle_runtime_error("Не удалось начать диктовку", exc)

    def start_hold_dictation(self) -> int | None:
        with self._lock:
            if self.settings.dictation.mode != DictationMode.PUSH_TO_TALK:
                return None
            return self._professional_session_number if self.start_dictation() else None

    def stop_hold_dictation(self, session: int) -> None:
        with self._lock:
            if (
                session == self._professional_session_number
                and self._session_mode == DictationMode.PUSH_TO_TALK
            ):
                self.stop_dictation()

    def _maybe_stop_after_silence(self, segmenter: StreamingSegmenter) -> None:
        if self._session_mode != DictationMode.AUTO_STOP or self._practice_language is not None:
            return
        started = self._capture_started_monotonic
        if started is None or self.state not in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
            AppState.PAUSED_TARGET_LOST,
        }:
            return
        scale = 1000 / segmenter.sample_rate
        if self._session_auto_stop.reached(
            (time.monotonic() - started) * 1000,
            segmenter.confirmed_speech_samples * scale,
            segmenter.trailing_silence_samples * scale,
        ):
            self.stop_dictation()

    def _stop_at_duration_limit(self, session: int) -> None:
        with self._lock:
            if (
                session == self._professional_session_number
                and self._session_mode == DictationMode.AUTO_STOP
            ):
                self.stop_dictation()

    def _cancel_duration_timer(self) -> None:
        if self._auto_stop_timer is not None:
            self._auto_stop_timer.cancel()
            self._auto_stop_timer = None

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
            self._cancel_duration_timer()
            was_starting = self.state in {AppState.STARTING, AppState.MODEL_LOADING}
            self._start_cancelled.set()
            self._session_stopped_monotonic = time.monotonic()
            self._live_transcript = None
            self.state_machine.transition(AppState.FINISHING, "user stopped dictation")
            self._status_message = (
                "Отменяю запуск" if was_starting else "Обрабатываю оставшийся звук"
            )
            capture = self._capture
            audio_queue = self._audio_queue
            if was_starting:
                if isinstance(capture, AudioCaptureService) and capture.pre_roll_armed:
                    stopper = threading.Thread(
                        target=capture.stop, name="voxtype-cancel-preroll", daemon=True
                    )
                    self._stop_thread = stopper
                    stopper.start()
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
            try:
                stopped = capture.stop(drain_timeout=10.0)
                stats = capture.stats()
            except Exception as exc:
                self._handle_runtime_error("Не удалось безопасно остановить микрофон", exc)
                return
            with self._lock:
                self._last_stats = stats
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
            journal = self._journal
            if journal is not None:
                finalization = (
                    capture.finalization if isinstance(capture, AudioCaptureService) else None
                )
                try:
                    journal.append(
                        "capture_stopped",
                        {}
                        if finalization is None
                        else {
                            "accepted_input_samples": finalization.last_accepted_sample,
                            "consumed_input_samples": finalization.last_consumed_sample,
                            "published_samples": finalization.last_published_sample,
                            "dropped_input_samples": finalization.dropped_samples,
                            "complete": finalization.complete,
                        },
                    )
                except (OSError, RuntimeError, sqlite3.Error):
                    self.logger.warning("capture_stop_journal_failed")
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
        try:
            action = self._queue_live_final()
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            self._handle_runtime_error("Не удалось сохранить итог диктовки", exc)
            return
        journal = self._journal
        if journal is not None and not self._processing_cancelled.is_set():
            try:
                journal.append(
                    "asr_finalized", {"has_text": bool(self.merger.committed_text.strip())}
                )
            except (OSError, RuntimeError, sqlite3.Error):
                self.logger.warning("session_finalization_journal_failed")
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
        self._complete_session("interrupted" if self._session_interrupted else "completed")
        resolution = self._live_command_resolution
        if action is not None and resolution is not None and self.professional_action is not None:
            self.professional_action(
                self._professional_session_number, action, self._session_profile, resolution
            )

    def set_system_unavailable(self, unavailable: bool) -> None:
        with self._lock:
            self._system_unavailable = unavailable
        if unavailable:
            self.target_manager.on_suspend()
        else:
            self.target_manager.on_resume()

    def interrupt_dictation(self) -> None:
        with self._lock:
            self._session_interrupted = True
            self._target_reselect_required = True
            self._live_transcript = None
            self.target_manager.end_session()
            if self._system_unavailable:
                self.target_manager.on_suspend()
        self.stop_dictation()

    def _queue_live_final(self) -> VoiceAction | None:
        with self._lock:
            live = self._live_session
            if (
                live is None
                or live.canceled
                or self._session_interrupted
                or self._processing_cancelled.is_set()
            ):
                return None
            text = self.merger.committed_text
            self._live_transcript = None
            final_revision = live.revision + 1
            action = self._resolve_live_command(live)
            if action is not None:
                inline = self._inline_command(action, "", live.language)
                if inline is not None or (
                    self.professional_action is not None
                    and action.action_type != "FormatStructured"
                ):
                    text = inline if inline is not None else ""
                    self.merger.set_final_text(text)
                    self._live_command_resolution = CommandResolution(
                        live.session_id,
                        final_revision,
                        True,
                        action.id,
                        tuple(action.payload.values()),
                        "",
                        action.confirmation_required,
                    )
                    if inline is not None:
                        self._live_command_claimed = True
                        action = None
                else:
                    action = None
            if not text and self._live_command_resolution is None:
                return None
            self._live_final = SpeechSegment(
                f"{live.session_id}:final",
                live.session_id,
                0,
                live.processed_offset,
                text,
                SpeechKind.FINAL,
                final_revision,
            )
            journal = self._journal
            if text and journal is not None and not self._live_text_committed:
                journal.commit_text(text)
                self._live_text_committed = True
            if action is not None:
                return action
            if (
                not self.settings.dictation.auto_insert
                or self._audio_review_required
                or self._local_only
                or not text
            ):
                return None
            delivery_id = self._live_final.segment_id
        self._enqueue_delivery(delivery_id, text, whole_result=True)
        return None

    def _resolve_live_command(self, live: LiveSpeechSession) -> VoiceAction | None:
        if (
            not self._live_command_text
            or not self.policy.allow_voice_commands
            or self._terminal_input
            or self._local_only
            or self._audio_review_required
            or self._system_unavailable
            or self._shutdown_requested.is_set()
        ):
            return None
        request = self._session_text_pipeline.request(
            self._professional_session_number,
            live.language,
            live.model_id,
            self._session_profile,
            self._session_context,
            (),
        )
        return self._session_text_pipeline.process(
            request, self._live_command_text, self._session_profile, command_mode=self._command_mode
        ).voice_action

    def _inline_command(self, action: VoiceAction, previous: str, language: str) -> str | None:
        if action.action_type == "FormatStructured":
            return StructuredDictationFormatter.apply(
                self._formatting_state, action.payload["command"], previous, language
            )
        return self._session_text_pipeline.inline_action(action, self._session_profile)

    def _enqueue_delivery(self, delivery_id: str, text: str, *, whole_result: bool = False) -> None:
        journal = self._journal
        durable = journal is not None and journal.persist_text
        try:
            if journal is not None:
                if durable:
                    self._outbox_session_id = journal.session_id
                if durable and not self.session_repository.queue_delivery(
                    delivery_id,
                    journal.session_id,
                    text,
                    self._session_profile.insertion_mode
                    or self.settings.dictation.injection_mode.value,
                    whole_result=whole_result,
                ):
                    return
                journal.append("delivery_queued", {"delivery_id": delivery_id, "durable": durable})
            self._delivery_queue.put_nowait((delivery_id, text, durable))
        except (Full, OSError, RuntimeError, sqlite3.Error):
            with self._lock:
                self._delivery_status = "failed"
                self._error_message = "Не удалось подготовить вставку; текст сохранён в результате"

    def cancel_processing(self) -> bool:
        with self._lock:
            if self.state is not AppState.FINISHING or self._session_id is None:
                return False
            self._processing_cancelled.set()
            if self._live_session is not None:
                self._live_session.canceled = True
            self._live_transcript = None
            self._status_message = "Останавливаю обработку; записанный текст сохранён"
            return True

    def _pipeline_loop(self) -> None:
        with self._lock:
            audio_queue = self._audio_queue
            segmenter = self._segmenter
        if audio_queue is None or segmenter is None:
            self._handle_runtime_error("Внутренняя аудиоочередь не создана", RuntimeError())
            return
        segmenter.session_id = audio_queue.session_id
        live = self._live_session
        preview: _LivePreviewJob | None = None
        quality = AudioQualityMonitor()
        pending: deque[AudioQueueLease] = deque()
        pending_seconds = 0.0
        input_overflow_reported = False
        consumed_offset = 0
        capture_gap = False

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
                    self._maybe_stop_after_silence(segmenter)
                    continue
                if lease.chunk.session_id not in {None, audio_queue.session_id}:
                    audio_queue.ack(lease)
                    continue
                offset = lease.chunk.sample_offset
                if offset is not None and offset != consumed_offset:
                    capture_gap = True
                consumed_offset = (
                    offset if offset is not None else consumed_offset
                ) + lease.chunk.frames
                if lease.chunk.input_overflow and not input_overflow_reported:
                    input_overflow_reported = True
                    self.logger.warning("audio_input_overflow session_id=%s", self._session_id)
                    with self._lock:
                        self._audio_review_required = True
                        self._delivery_status = "copy_required"
                        self._status_message = "Микрофон потерял часть звука; запись продолжается"
                    if self._session_id is not None:
                        self.session_repository.add_warning(self._session_id, "AudioInputOverflow")
                if self._captured_during_pause(lease.chunk.captured_at):
                    audio_queue.ack(lease)
                    continue
                pending.append(lease)
                pending_seconds += lease.chunk.duration_seconds
                archive = self._audio_archive
                if archive is not None:
                    pcm16 = (lease.chunk.samples * np.float32(32_767.0)).astype("<i2")
                    archive.writeframesraw(pcm16.tobytes())
                prior_speech, prior_frames = segmenter.speech_samples, segmenter.analyzed_samples
                segments = segmenter.process(lease.chunk.samples)
                observation = lease.chunk.input_quality
                if observation is not None:
                    speech = (
                        True
                        if segmenter.speech_samples > prior_speech
                        else False
                        if segmenter.analyzed_samples > prior_frames and not segmenter.active
                        else None
                    )
                    status = quality.observe(observation, speech=speech)
                    if status is not None:
                        with self._lock:
                            self._audio_quality = status
                self._maybe_stop_after_silence(segmenter)
                if live is not None:
                    live.offer(lease.chunk.samples)
                    if segments:
                        live.finish_segment(segmenter.speech_samples)
                        with self._lock:
                            self._live_transcript = None
                if segments and preview is not None:
                    self._finish_live_preview(preview)
                    preview = None
                for segment in segments:
                    self._transcribe_segment(segment, self._worker_language)
                ack_committed_prefix()
                if live is not None and not segments:
                    if preview is not None and self._publish_live_preview(live, preview):
                        preview = None
                    if (
                        preview is None
                        and segmenter.active
                        and (
                            audio_queue.try_snapshot().queued_audio_seconds - pending_seconds < 0.2
                        )
                    ):
                        preview = self._start_live_preview(live, segmenter.speech_samples)
            if preview is not None:
                self._finish_live_preview(preview)
            capture = self._capture
            if isinstance(capture, AudioCaptureService) and capture.finalization is not None:
                finalization = capture.finalization
                if capture_gap or consumed_offset != finalization.last_published_sample:
                    raise AudioIngressOverflowError(
                        "The VAD did not receive the complete published audio"
                    )
                if not finalization.complete or input_overflow_reported:
                    with self._lock:
                        self._audio_review_required = True
                        self._error_message = (
                            "Часть звука могла быть потеряна. Проверьте результат."
                        )
                    if self._session_id is not None:
                        self.session_repository.add_warning(self._session_id, "IncompleteAudio")
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

    def _start_live_preview(
        self, live: LiveSpeechSession, speech_samples: int
    ) -> _LivePreviewJob | None:
        with self._lock:
            backend = self._session_backend
            if (
                backend is None
                or self.state is not AppState.LISTENING
                or self._session_interrupted
                or self._inference_thread is not None
            ):
                return None
        started = time.monotonic()
        request = live.request(speech_samples, started)
        if request is None:
            return None
        results = self._start_inference(lambda: backend.transcribe(request))
        return _LivePreviewJob(request, started, results)

    def _publish_live_preview(self, live: LiveSpeechSession, job: _LivePreviewJob) -> bool:
        try:
            result = job.results.get_nowait()
        except Empty:
            if time.monotonic() - job.started >= self.inference_timeout_s:
                raise InferenceTimeoutError(
                    "live preview inference exceeded the native inference deadline"
                ) from None
            return False
        if isinstance(result, BaseException):
            self.logger.debug("live_preview_failed kind=%s", type(result).__name__)
            return True
        with self._lock:
            if (
                self._live_session is live
                and self.state is AppState.LISTENING
                and not live.canceled
                and not self._session_interrupted
                and not self._processing_cancelled.is_set()
            ):
                live.publish(job.request, result, time.monotonic() - job.started)
                self._live_transcript = live.snapshot
        return True

    def _finish_live_preview(self, job: _LivePreviewJob) -> None:
        # A final segment has priority. Discard preview output and wait only
        # for the existing native call, using its original deadline.
        self._wait_inference(
            job.results, self._processing_cancelled, job.started + self.inference_timeout_s
        )

    def _transcribe_segment(self, segment: AudioSegment, language: Language) -> None:
        if not segment.samples.size or segment.session_id not in {
            None,
            self._professional_session_number,
        }:
            return
        if segment.sequence == 0 and self._journal is not None:
            self._journal.append("asr_started", {"generation": self._professional_session_number})
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
            self._professional_session_number,
            language.value,
            (self._session_backend or self.backend).model_id,
            self._session_profile,
            self._session_context,
            self.vocabulary.entries,
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
            hotwords=professional_request.hotwords,
            condition_on_previous_text=False,
            metadata={
                "kind": "final",
                "segment_sequence": str(segment.sequence),
                "session_id": str(self._professional_session_number),
            },
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
            if result.text:
                self._raw_asr_parts.append(result.text)
            self._recognized_segments.extend(result.segments)
        self.logger.debug(
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
            live = self._live_session is not None
            if live and self._live_command_text is not None:
                candidate = f"{self._live_command_text} {novel_text}".strip()
                self._live_command_text = candidate if len(candidate) <= 2048 else None
            processed = self._session_text_pipeline.process(
                professional_request,
                novel_text,
                self._session_profile,
                command_mode=self._command_mode,
                allow_commands=(
                    self.policy.allow_voice_commands
                    and self._practice_language is None
                    and not self._terminal_input
                    and not self._local_only
                    and not live
                ),
            )
            action = processed.voice_action
            if action is None:
                return processed.final_text
            if action.action_type == "InsertSnippet" and not self.policy.allow_user_snippets:
                return processed.normalized_text
            inline = self._inline_command(action, committed, language.value)
            if inline is not None:
                return inline
            if action.action_type == "FormatStructured":
                return processed.normalized_text
            if self.professional_action is not None and self._live_session is None:
                deferred_action = action
                return ""
            return processed.normalized_text

        mode = self._session_profile.formatting_mode
        if self._session_profile.output_language == OutputLanguagePolicy.PRESERVE_SPOKEN_LANGUAGE:
            mode = "code"
        outcome = self.merger.merge(
            fragment,
            add_space=self.settings.dictation.add_fragment_space,
            capitalize=self.settings.dictation.capitalize_sentences
            and mode not in {"code", "verbatim"},
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
            journal.append(
                "raw_asr",
                {
                    "schema_version": 1,
                    "segment_sequence": segment.sequence,
                    "text": result.text,
                },
            )
        if deferred_action is not None and self.professional_action is not None:
            self.professional_action(
                professional_request.session_id, deferred_action, self._session_profile, None
            )
        if guarded.assessment.suspicious and journal is not None:
            warning = ",".join(issue.value for issue in guarded.assessment.issues)
            self.session_repository.add_warning(
                self._require_session_id(), warning, suspicious=True
            )
        if outcome.insertion_text:
            if journal is None:
                raise RuntimeError("committed text has no active journal")
            if self._live_session is None:
                journal.commit_text(outcome.insertion_text)
            with self._lock:
                self._last_fragment = outcome.insertion_text.strip()
                self._provisional = ""
                clipping = self._audio_quality is not None and self._audio_quality.clipping_detected
                if guarded.assessment.score >= 4 or (clipping and guarded.assessment.suspicious):
                    self._audio_review_required = True
                    self._error_message = (
                        "Распознавание неуверенное. Проверьте результат перед вставкой."
                    )
                auto_insert = (
                    self.settings.dictation.auto_insert
                    and self._practice_language is None
                    and self._live_session is None
                    and not self._session_interrupted
                    and not self._audio_review_required
                    and not self._local_only
                )
                if self._audio_review_required:
                    self._delivery_status = "copy_required"
                    self._status_message = "Проверьте результат перед вставкой"
                elif not auto_insert:
                    self._delivery_status = "pending"
                    if self._session_interrupted:
                        self._status_message = "Сеанс прерван; накопленный текст доступен в VoxType"
                    elif (
                        self._live_session is not None
                        and self.settings.dictation.auto_insert
                        and not self._local_only
                    ):
                        self._status_message = "Текст будет отправлен после остановки"
                    else:
                        self._status_message = (
                            "Текст сохранён в истории; автовставка выключена"
                            if journal.persist_text
                            else "Текст доступен в результате; автовставка выключена"
                        )
                else:
                    self._delivery_status = "pending"
                    self._status_message = "Подтверждённый текст ожидает вставки"
            if auto_insert:
                fragment_id = f"{journal.session_id}:{segment.sequence}"
                self._enqueue_delivery(fragment_id, outcome.insertion_text)
        else:
            with self._lock:
                self._provisional = ""
        self.state_machine.transition_if(
            {AppState.TRANSCRIBING, AppState.SPEECH_DETECTED},
            AppState.LISTENING,
            "segment committed",
        )

    def _record_delivery_outcome(
        self,
        delivery_id: str,
        durable: bool,
        status: str,
        error: str | None = None,
    ) -> None:
        if durable:
            try:
                self.session_repository.finish_delivery(delivery_id, status, error)
            except (OSError, sqlite3.Error):
                # Leave ATTEMPTING intact; a missing outcome must never be retried automatically.
                self.logger.warning("delivery_outcome_write_failed")

    def _delivery_loop(self) -> None:
        """Single bounded FIFO owner for every automatic target insertion."""

        while True:
            item = self._delivery_queue.get()
            owns_delivery = False
            try:
                if item is None:
                    return
                fragment_id, text, durable = item
                with self._lock:
                    if (
                        self._processing_cancelled.is_set()
                        or self._session_interrupted
                        or self._system_unavailable
                        or self._audio_review_required
                        or self._session_id != fragment_id.rsplit(":", 1)[0]
                    ):
                        continue
                    self._delivery_busy = True
                    owns_delivery = True
                try:
                    if durable and not self.session_repository.claim_delivery(fragment_id):
                        continue
                    journal = self._journal
                    if journal is not None:
                        journal.append("delivery_attempted", {"delivery_id": fragment_id})
                    injection = (
                        self.injection_service.submit(
                            text,
                            fragment_id=fragment_id,
                            exact_text=self._session_profile.formatting_mode
                            in {"code", "verbatim"},
                            strategy=self._session_profile.insertion_mode or None,
                        )
                        if isinstance(self.injection_service, TextInjectionService)
                        else self.injection_service.submit(text)
                    )
                except Exception as exc:
                    self._record_delivery_outcome(
                        fragment_id, durable, "failed", type(exc).__name__
                    )
                    self.logger.warning("delivery_failed kind=%s", type(exc).__name__)
                    with self._lock:
                        self._delivery_status = "failed"
                        self._error_message = "Вставка не удалась; весь текст сохранён"
                    continue
                if injection.status == InjectionStatus.DUPLICATE:
                    continue
                delivery_status = (
                    "submitted_unconfirmed"
                    if injection.status in {InjectionStatus.INSERTED, InjectionStatus.UNKNOWN}
                    else "failed"
                )
                self._record_delivery_outcome(
                    fragment_id, durable, delivery_status, injection.status.value
                )
                with self._lock:
                    if self._processing_cancelled.is_set():
                        continue
                    needs_manual = (
                        injection.status is InjectionStatus.BLOCKED
                        and injection.target_status is TargetStatus.READY
                    )
                    if needs_manual:
                        self._delivery_status = "copy_required"
                        self._status_message = injection.detail
                        self._error_message = injection.detail
                    elif injection.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                        self._delivery_status = "queued_target_inactive"
                        reason = (
                            injection.detail
                            if injection.target_status is TargetStatus.READY and injection.detail
                            else self._target_warning(injection.target_status)
                        )
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
                        self._status_message = "Команда вставки отправлена"
                        self._error_message = ""
                    elif injection.status is InjectionStatus.COPIED:
                        self._delivery_status = "copied"
                        self._error_message = ""
                if not needs_manual and injection.status in {
                    InjectionStatus.QUEUED,
                    InjectionStatus.BLOCKED,
                }:
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
        with self._lock:
            return self.set_language(self._language.toggled())

    def set_language(self, language: Language, *, persist: bool = True) -> Language:
        with self._lock:
            self._language = language
            self.settings.dictation.language = language
            if persist:
                self.settings_repository.save(self.settings)
            return language

    def can_change_runtime_settings(self) -> bool:
        with self._lock:
            status = self._readiness()
            return status.phase in {
                AppPhase.READY,
                AppPhase.ERROR,
                AppPhase.MODEL_REQUIRED,
                AppPhase.MICROPHONE_UNAVAILABLE,
            } and (
                self.state in {AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE}
                and self._session_started_monotonic is None
                and self._inference_thread is None
                and not self._file_recognition_active
                and not self._recovery_active
            )

    def _transcribe_guarded_with_timeout(
        self,
        request: TranscriptionRequest,
        committed_text: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> GuardedTranscription:
        """Run native inference behind one daemon watchdog per serial chunk."""

        backend = (self._session_backend if cancel_event is not None else None) or self.backend
        retry_backend = self.retry_backend if backend is self.backend else None
        if retry_backend is not None and not self.policy.model_allowed(retry_backend.model_id):
            retry_backend = None

        return self._run_inference(
            lambda: self.guard.transcribe_guarded(
                backend,
                request,
                committed_text=committed_text,
                retry_backend=retry_backend,
            ),
            cancel_event,
        )

    def _start_inference[T](self, call: Callable[[], T]) -> Queue[T | BaseException]:
        results: Queue[T | BaseException] = Queue(maxsize=1)

        def worker() -> None:
            try:
                outcome: T | BaseException = call()
            except BaseException as exc:  # propagate native/backend failures to pipeline owner
                outcome = exc
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
        return results

    def _run_inference[T](self, call: Callable[[], T], cancel_event: threading.Event | None) -> T:
        results = self._start_inference(call)
        outcome = self._wait_inference(
            results, cancel_event, time.monotonic() + self.inference_timeout_s
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _wait_inference[T](
        self,
        results: Queue[T | BaseException],
        cancel_event: threading.Event | None,
        deadline: float,
    ) -> T | BaseException:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ProcessingCancelled()
            try:
                return results.get_nowait()
            except Empty:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InferenceTimeoutError(
                    f"local inference exceeded {self.inference_timeout_s:.1f} seconds"
                )
            try:
                return results.get(timeout=min(remaining, 0.1) if cancel_event else remaining)
            except Empty:
                continue

    def configure_backend(
        self,
        backend: ASRBackend,
        profile: ModelProfile,
        retry_backend: ASRBackend | None = None,
    ) -> bool:
        """Load a candidate before replacing the active model."""
        with self._lock:
            if (
                not self.policy.model_allowed(backend.model_id)
                or not self.can_change_runtime_settings()
            ):
                return False
            self._model_switch_active = True
        try:
            try:
                backend.load()
            except Exception as exc:
                self.logger.warning("model_activation_failed kind=%s", type(exc).__name__)
                with self._lock:
                    self._error_message = (
                        "Не удалось загрузить модель. Предыдущая модель сохранена."
                    )
                return False
            if self._shutdown_requested.is_set():
                backend.unload()
                return False
            previous = self.backend
            previous_retry = self.retry_backend
            shares_resident_model = getattr(backend, "shares_resident_model", lambda other: False)
            try:
                self.settings_repository.update(
                    {
                        "dictation": {"model_profile": profile.value},
                        "model": {"installed_model": backend.model_id},
                    }
                )
            except (OSError, ValueError, TypeError):
                if backend is not previous and not shares_resident_model(previous):
                    backend.unload()
                with self._lock:
                    self._error_message = "Не удалось сохранить выбранную модель."
                return False
            with self._lock:
                self.settings.dictation.model_profile = profile
                self.settings.model.installed_model = backend.model_id
                self.backend = backend
                self._loaded_backend = backend
                self._model_available = True
                self.retry_backend = retry_backend
                self._status_message = f"Активная модель: {backend.model_id}"
                self._error_message = ""
                if self.state is AppState.ERROR_RECOVERABLE:
                    self.state_machine.transition(AppState.IDLE, "installed model selected")
            if previous is not backend and not shares_resident_model(previous):
                previous.unload()
            if (
                previous_retry is not None
                and previous_retry not in (previous, backend, retry_backend)
                and not shares_resident_model(previous_retry)
            ):
                previous_retry.unload()
            return True
        finally:
            with self._lock:
                self._model_switch_active = False

    def profile_allowed(self, profile: DictationProfile) -> bool:
        return (
            profile.enabled
            and self.policy.profile_allowed(profile.id)
            and self.policy.model_allowed(profile.model_id or self.backend.model_id)
        )

    @property
    def active_model_loaded(self) -> bool:
        return bool(getattr(self.backend, "loaded", self._loaded_backend is self.backend))

    @property
    def any_asr_model_loaded(self) -> bool:
        return bool(getattr(self.backend, "any_model_loaded", self.active_model_loaded))

    def reserve_audio_monitor(self) -> bool:
        with self._lock:
            if (
                self._system_unavailable
                or self._audio_monitor_active
                or not self.can_change_runtime_settings()
            ):
                return False
            self._audio_monitor_active = True
            return True

    def release_audio_monitor(self) -> None:
        with self._lock:
            self._audio_monitor_active = False

    def reserve_file_recognition(self) -> threading.Event | None:
        with self._lock:
            if (
                self._shutdown_requested.is_set()
                or self._recovery_active
                or self._file_recognition_active
                or self._audio_monitor_active
                or self._model_switch_active
                or self._session_id is not None
                or self._inference_thread is not None
                or self.state not in {AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE}
                or any(
                    worker is not None and worker.is_alive()
                    for worker in (
                        self._start_thread,
                        self._stop_thread,
                        self._finish_thread,
                        self._preload_thread,
                    )
                )
            ):
                return None
            self._file_recognition_active = True
            self._file_recognition_cancel.clear()
            return self._file_recognition_cancel

    def release_file_recognition(self) -> None:
        with self._lock:
            self._file_recognition_active = False

    def transcribe_professional_file(
        self, source: Path, profile_id: str, language: str, job_id: str
    ):
        from voxtype.asr.file_transcription import FileTranscriptionService

        profile = next(
            (
                item
                for item in self.profiles.profiles()
                if item.id == profile_id and self.profile_allowed(item)
            ),
            None,
        )
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
            int(job_id[:16], 16),
            selected_language,
            backend.model_id,
            profile,
            context,
            self.settings.dictation.custom_vocabulary,
        )
        return FileTranscriptionService().transcribe(
            source,
            backend,
            selected_language,
            self._file_recognition_cancel,
            initial_prompt=request.initial_prompt or "",
            hotwords=request.hotwords,
            transform=lambda text: (
                pipeline.process(request, text, profile, allow_commands=False).final_text
            ),
        )

    def journal_file_result(
        self,
        job_id: str,
        profile_id: str,
        language: str,
        duration: float,
        text: str,
    ) -> None:
        if (
            self.policy.disable_history
            or not self.settings.privacy.save_history
            or not text.strip()
        ):
            return
        profile = next((item for item in self.profiles.profiles() if item.id == profile_id), None)
        self.session_repository.save_file_record(
            job_id,
            language,
            (profile.model_id if profile else "") or self.backend.model_id,
            profile_id,
            self.settings.dictation.model_profile.value,
            duration,
            text,
        )

    def recover_session(self, session_id: str) -> str:
        """Replay crash-safe PCM and append only the missing transcript tail."""

        with self._lock:
            if (
                self._shutdown_requested.is_set()
                or not self.can_change_runtime_settings()
                or self._system_unavailable
                or self._session_id is not None
                or self.policy.disable_history
                or self.state not in {AppState.IDLE, AppState.COMPLETED, AppState.ERROR_RECOVERABLE}
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
        if record.source_type == "draft" and not self.settings.privacy.recover_unsaved_drafts:
            raise PermissionError("draft recovery is disabled")
        from uuid import UUID

        if UUID(session_id).hex != session_id.replace("-", "").lower():
            raise ValueError("Invalid recovery session ID")
        spool_directory = (self.spool_root / session_id).resolve()
        if not spool_directory.is_relative_to(self.spool_root.resolve()):
            raise ValueError("Recovery path is outside VoxType storage")
        if self.policy.disable_audio_storage or not spool_directory.exists():
            self.session_repository.finish(session_id, status="recovered")
            return record.committed_text

        spool = DiskSpool(spool_directory, max_bytes=4 * 1024**3)
        segmenter = self.segmenter_factory()
        merger = TranscriptMerger()
        language = Language(record.language)
        profile = next(
            (item for item in self.profiles.profiles() if item.id == record.profile_id),
            BUILTIN_PROFILES[0],
        )
        context = DictationContext(
            executable_name=record.target_application,
            selection_mode="recovery",
            profile_id=profile.id,
        )
        pipeline = self.professional_content.snapshot()
        switches = deque(self.session_repository.language_switches(session_id))
        leased_sequences: list[int] = []
        try:
            self.backend.load()
            while (chunk := spool.acquire()) is not None:
                leased_sequences.append(chunk.sequence)
                while switches and chunk.captured_at >= switches[0][0]:
                    for segment in segmenter.flush():
                        self._merge_recovered_segment(
                            segment,
                            language,
                            record.committed_text,
                            merger,
                            pipeline,
                            profile,
                            context,
                        )
                    segmenter.reset(preserve_sequence=True)
                    _captured_at, language_code = switches.popleft()
                    language = Language(language_code)
                for segment in segmenter.process(chunk.samples):
                    self._merge_recovered_segment(
                        segment, language, record.committed_text, merger, pipeline, profile, context
                    )
            for segment in segmenter.flush():
                self._merge_recovered_segment(
                    segment, language, record.committed_text, merger, pipeline, profile, context
                )

            recovered = deduplicate_text_boundary(record.committed_text, merger.committed_text)
            mode = (
                "code"
                if profile.output_language == OutputLanguagePolicy.PRESERVE_SPOKEN_LANGUAGE
                else profile.formatting_mode
            )
            insertion = format_fragment_boundary(
                record.committed_text,
                recovered,
                add_space=self.settings.dictation.add_fragment_space
                and not (
                    mode == "code"
                    and (record.committed_text.endswith("_") or recovered.startswith(("_", "(")))
                ),
                capitalize=self.settings.dictation.capitalize_sentences
                and mode not in {"code", "verbatim"},
                normalize=mode not in {"code", "verbatim"},
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
                updated.committed_text
                if updated is not None and persists
                else record.committed_text + insertion
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
        pipeline: TextPipeline,
        profile: DictationProfile,
        context: DictationContext,
    ) -> None:
        prompt = previous_text + merger.committed_text
        professional = pipeline.request(
            0,
            language.value,
            self.backend.model_id,
            profile,
            context,
            self.vocabulary.entries,
        )
        request = TranscriptionRequest(
            audio=segment.samples,
            sample_rate=segment.sample_rate,
            language=language.value,
            start_offset_seconds=segment.start_seconds,
            initial_prompt=bounded_prompt(prompt, professional.hotwords),
            hotwords=professional.hotwords,
            condition_on_previous_text=False,
            metadata={"segment_sequence": str(segment.sequence), "recovery": "true"},
        )
        if self._shutdown_requested.is_set():
            raise RuntimeError("shutdown requested during session recovery")
        guarded = self._transcribe_guarded_with_timeout(request, prompt)
        result = guarded.result
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
        mode = (
            "code"
            if profile.output_language == OutputLanguagePolicy.PRESERVE_SPOKEN_LANGUAGE
            else profile.formatting_mode
        )
        merger.merge(
            fragment,
            add_space=self.settings.dictation.add_fragment_space,
            capitalize=self.settings.dictation.capitalize_sentences
            and mode not in {"code", "verbatim"},
            code_spacing=mode == "code",
            preserve_format=mode in {"code", "verbatim"},
            transform=lambda text: (
                pipeline.process(professional, text, profile, allow_commands=False).final_text
            ),
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

    def restart_dictation(self) -> bool:
        with self._lock:
            if self.state is AppState.ERROR_RECOVERABLE:
                if not self._readiness().can_retry:
                    return False
                self.state_machine.transition(AppState.IDLE, "user requested recovery")
            return self.start_dictation()

    @property
    def resource_phase(self) -> ResourcePhase:
        with self._lock:
            if self._shutdown_complete:
                return ResourcePhase.STOPPED
            if self._shutdown_requested.is_set():
                return ResourcePhase.SHUTTING_DOWN
            if not self._initialized:
                return ResourcePhase.COLD
            if self._session_id is not None or self._file_recognition_active:
                if self.state is AppState.FINISHING:
                    return ResourcePhase.QUIESCING
                return ResourcePhase.ACTIVE
            if self._model_switch_active or self.state in {
                AppState.STARTING,
                AppState.MODEL_LOADING,
            }:
                return ResourcePhase.INITIALIZING
            if (
                self._recovery_active
                or self._delivery_busy
                or self._inference_thread is not None
                or any(
                    worker is not None and worker.is_alive()
                    for worker in (
                        self._start_thread,
                        self._stop_thread,
                        self._finish_thread,
                        self._pipeline_thread,
                    )
                )
            ):
                return ResourcePhase.QUIESCING
            return ResourcePhase.READY

    def release_idle_models(self) -> bool:
        """Release resident models on a worker while the inference gate is closed."""
        with self._lock:
            if not self.can_change_runtime_settings() or self._delivery_busy:
                return False
            self._model_switch_active = True
            backends = (self.backend, self.retry_backend, self._file_backend)
        try:
            unload_resident = getattr(self.backend, "unload_resident", None)
            if unload_resident is not None:
                unload_resident()
            else:
                for index, backend in enumerate(backends):
                    if backend is not None and not any(
                        backend is prior for prior in backends[:index]
                    ):
                        backend.unload()
            return True
        finally:
            with self._lock:
                self._loaded_backend = None
                self._model_switch_active = False

    def recording_readiness(self) -> AppReadiness:
        with self._lock:
            return self._readiness()

    def active_profile_id(self) -> str:
        with self._lock:
            if self._session_started_monotonic is not None:
                return self._session_profile.id
        return self.profiles.default_id

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
        elif self._model_switch_active or state is AppState.MODEL_LOADING:
            phase, reason, message = AppPhase.MODEL_LOADING, "model_loading", "Загрузка модели"
        elif state is AppState.FINISHING:
            phase, reason, message = AppPhase.PROCESSING, "processing", "Обработка записи"
        elif self._audio_monitor_active:
            phase, reason, message = AppPhase.PROCESSING, "audio_monitor", "Проверка микрофона"
        elif self._file_recognition_active:
            phase, reason, message = (
                AppPhase.PROCESSING,
                "file_processing",
                "Распознавание аудиофайла",
            )
        elif state in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
            AppState.PAUSED,
            AppState.PAUSED_TARGET_LOST,
        }:
            phase, reason, message = AppPhase.RECORDING, "recording", "Запись"
        elif self._delivery_busy:
            phase, reason, message = AppPhase.INSERTING, "inserting", "Отправка текста"
        elif self._system_unavailable:
            phase, reason, message = (
                AppPhase.MICROPHONE_UNAVAILABLE,
                "device_recovery",
                "Переподключение микрофона",
            )
        elif not self._model_available:
            phase, reason, message = AppPhase.MODEL_REQUIRED, "model_missing", "Требуется модель"
        elif not self._microphone_available:
            phase, reason, message = (
                AppPhase.MICROPHONE_UNAVAILABLE,
                "microphone_missing",
                "Микрофон недоступен",
            )
        elif state in {AppState.ERROR_RECOVERABLE, AppState.ERROR_FATAL}:
            phase, reason, message = AppPhase.ERROR, "operation_failed", "Требуется внимание"
        elif busy:
            phase, reason, message = AppPhase.PROCESSING, "worker_busy", "Завершение операции"
        return AppReadiness(
            phase,
            reason,
            message,
            not busy and phase is AppPhase.READY and state in {AppState.IDLE, AppState.COMPLETED},
            state is AppState.FINISHING
            and self._session_id is not None
            and not self._processing_cancelled.is_set(),
            session_id=self._professional_session_number
            if self._session_started_monotonic is not None
            else None,
            can_retry=not busy and phase is AppPhase.ERROR and state is AppState.ERROR_RECOVERABLE,
        )

    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            capture = self._capture
            stats = (
                capture.try_stats()
                if isinstance(capture, AudioCaptureService)
                else capture.stats()
                if capture is not None
                else self._last_stats
            )
            started = self._session_started_monotonic
            stopped = self._session_stopped_monotonic
            elapsed = (
                (stopped if stopped is not None else time.monotonic()) - started
                if started is not None
                else self._completed_duration_s
            )
            # Presentation uses the last captured target. Delivery resolves and
            # validates the current Windows target independently.
            if isinstance(self.target_manager, TargetWindowManager):
                target = self.target_manager.display_target
            else:
                target = self.target_manager.locked_target
            current_language = (
                self._worker_language if self._session_id is not None else self._language
            )
            current_model = (self._session_backend or self.backend).model_id
            return ControllerSnapshot(
                state=self.state.name,
                language=current_language,
                session_elapsed_s=max(0.0, elapsed),
                queued_audio_seconds=stats.queued_audio_seconds if stats is not None else 0.0,
                last_committed_text="" if self._system_unavailable else self._last_fragment,
                provisional_text="" if self._system_unavailable else self._provisional,
                target_title=target.title if target is not None else "",
                input_rms=stats.current_input_rms if stats is not None else 0.0,
                clipping_percentage=stats.clipping_percentage if stats is not None else 0.0,
                warning=self._status_message,
                microphone_name=self.microphone_name,
                model_name=current_model,
                model_profile=self.settings.dictation.model_profile,
                status_message=self._status_message,
                error_message=self._error_message,
                full_transcript=""
                if self._system_unavailable
                else (self.merger.committed_text or self._completed_transcript),
                delivery_status=self._delivery_status,
                readiness=self._readiness(),
                recognition_result=None if self._system_unavailable else self._recognition_result,
                configured_language=self._language,
                live_transcript=None if self._system_unavailable else self._live_transcript,
                live_final=None if self._system_unavailable else self._live_final,
                audio_quality=self._audio_quality,
                pending_insertion=bool(self._pending_insertion_count),
            )

    def resource_counters(self) -> dict[str, int | float | bool]:
        """Return transient counters only, with no text or audio data."""
        with self._lock:
            capture = self._capture
            audio = (
                capture.try_stats()
                if isinstance(capture, AudioCaptureService)
                else capture.stats()
                if capture is not None
                else self._last_stats
            )
            workers = (
                self._start_thread,
                self._stop_thread,
                self._finish_thread,
                self._pipeline_thread,
                self._preload_thread,
                self._inference_thread,
                self._pause_thread,
            )
            asr_active = self._inference_thread is not None or self._file_recognition_active
            active = sum(worker is not None and worker.is_alive() for worker in workers)
            active += int(self._file_recognition_active) + int(self._delivery_thread.is_alive())
        return {
            "workers": active,
            "asr_active": int(asr_active),
            "audio_chunks": audio.ram_chunks + audio.spooled_chunks if audio else 0,
            "audio_seconds": audio.queued_audio_seconds if audio else 0.0,
            "dropped_frames": audio.dropped_frames if audio else 0,
            "overflows": audio.callback_overflows if audio else 0,
            "resident_model": self.any_asr_model_loaded,
        }

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
        self.logger.error("runtime_error phase=%s kind=%s", self.state.name, type(error).__name__)
        if isinstance(error, ASRModelUnavailable):
            code = (
                FailureCode.MODEL_LOAD_FAILED
                if self._model_available
                else FailureCode.MODEL_MISSING
            )
            user_message = FAILURES[code].text()
        elif (
            isinstance(error, AudioIngressOverflowError) or type(error).__name__ == "PortAudioError"
        ):
            code = FailureCode.AUDIO_CAPTURE_FAILED
            if len(error.args) > 1 and error.args[1] == -9985:
                code = FailureCode.MICROPHONE_BUSY
            user_message = FAILURES[code].text()
        elif isinstance(error, InferenceTimeoutError):
            user_message = FAILURES[FailureCode.ASR_FAILED].text()
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
            try:
                capture.stop(drain_timeout=2.0)
            except Exception as exc:
                self.logger.warning("audio_cleanup_failed kind=%s", type(exc).__name__)
        if audio_queue is not None:
            audio_queue.close()
        if session_id is not None:
            try:
                self.session_repository.add_warning(session_id, type(error).__name__)
            except (KeyError, OSError, sqlite3.Error) as exc:
                self.logger.warning("session_warning_failed kind=%s", type(exc).__name__)
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
            recoverable = self._practice_language is None and (
                self._spool is not None
                or (self._journal is not None and self._journal.persist_text)
            )
            self._finalize_session(
                "recovery_required" if recoverable else "failed", requires_recovery=recoverable
            )

    def handle_ui_exception(self, error: BaseException) -> None:
        """Convert an uncaught Qt callback exception into a recoverable state."""

        self._handle_runtime_error("Ошибка интерфейса; накопленный текст сохранён", error)

    def _complete_session(self, status: str) -> None:
        # A completed recording is a durable user-visible state. Target loss must
        # not keep the UI in FINISHING for 30 seconds: the FIFO remains available
        # for explicit retry/copy from the completed-result panel.
        self._finalize_session(status)

    def _reconcile_outbox_retry(self) -> None:
        session_id = self._outbox_session_id
        service = self.injection_service
        if session_id is None or not isinstance(service, TextInjectionService):
            return
        try:
            delivered = tuple(
                identifier
                for identifier in self.session_repository.queued_retry_ids(session_id)
                if service.delivery_status(identifier) is DeliveryStatus.DELIVERED
            )
            if delivered:
                self.session_repository.settle_queued_retry(delivered)
        except (OSError, sqlite3.Error):
            self.logger.warning("delivery_retry_outcome_write_failed")

    def flush_pending_text(self) -> bool:
        if self._system_unavailable or self._target_reselect_required:
            return False
        result = self.injection_service.flush()
        inserted = result.status is InjectionStatus.INSERTED
        if inserted:
            self._reconcile_outbox_retry()
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
                and not self._system_unavailable
                and not self._session_interrupted
                and not self._shutdown_requested.is_set()
                and not self._processing_cancelled.is_set()
            )

    def claim_live_command(self, number: int, resolution: CommandResolution) -> bool:
        with self._lock:
            if (
                not self.professional_session_is_current(number)
                or self._live_command_resolution != resolution
                or self._live_command_claimed
            ):
                return False
            self._live_command_claimed = True
            return True

    def cancel_professional_session(self, number: int) -> bool:
        if not self.professional_session_is_current(number):
            return False
        self.stop_dictation()
        return self.cancel_processing()

    def insert_professional_text(
        self,
        text: str,
        number: int | None = None,
        *,
        multiline_confirmed: bool = False,
        delivery_id: str | None = None,
    ) -> bool:
        if not text:
            return False
        with self._lock:
            if (
                self._shutdown_requested.is_set()
                or self._delivery_busy
                or self._system_unavailable
                or (number is not None and not self.professional_session_is_current(number))
                or (number is None and self.state not in {AppState.IDLE, AppState.COMPLETED})
                or (
                    delivery_id is not None
                    and (
                        self.policy.disable_history
                        or not self.settings.privacy.save_history
                        or self._pending_insertion_count > 0
                    )
                )
            ):
                return False
            profile = self._session_profile
            if number is None:
                decision = self.target_manager.start_session()
                target = getattr(decision, "target", None)
                if target is None:
                    return False
                profile_context = DictationContext.capture(
                    target, self.settings.dictation.target_mode.value
                )
                if self.policy.blocked(profile_context):
                    return False
                profile = self.profiles.resolve(profile_context)
                if not self.policy.profile_allowed(profile.id):
                    return False
            journal = self._journal if number is not None and self._session_id is not None else None
            self._delivery_busy = True
        claimed = False
        started_submission = False
        try:
            if (
                isinstance(self.injection_service, TextInjectionService)
                and self.injection_service.context_provider
            ):
                decision = self.target_manager.resolve()
                if number is None and decision.target is not None and decision.can_inject:
                    context = self.injection_service.context_provider.capture(decision.target)
                    if context is None:
                        return False
                    profile_context = DictationContext.capture(decision.target, context.target_mode)
                    self.injection_service.begin_adaptive_input(
                        context,
                        self.profiles.compatibility_rule(profile, profile_context),
                        terminal_safe=self.settings.dictation.safe_terminal_input,
                    )
            else:
                self.target_manager.try_restore_focus()
            if journal is not None:
                journal.commit_text(text)
            if delivery_id is not None:
                claimed = self.session_repository.claim_delivery(delivery_id, explicit=True)
                if not claimed:
                    return False
            started_submission = True
            if isinstance(self.injection_service, TextInjectionService):
                result = self.injection_service.submit(
                    text,
                    exact_text=True,
                    strategy=profile.insertion_mode or None,
                    manual=number is None,
                    multiline_confirmed=multiline_confirmed,
                )
            else:
                result = self.injection_service.submit(text)
            if claimed and delivery_id is not None:
                status = (
                    "submitted_unconfirmed"
                    if result.status in {InjectionStatus.INSERTED, InjectionStatus.UNKNOWN}
                    else "failed"
                )
                self._record_delivery_outcome(delivery_id, True, status, result.status.value)
            with self._lock:
                self._delivery_status = {
                    InjectionStatus.INSERTED: "delivered",
                    InjectionStatus.COPIED: "copied",
                    InjectionStatus.QUEUED: "queued_target_inactive",
                }.get(result.status, "copy_required")
            return result.status in {
                InjectionStatus.INSERTED,
                InjectionStatus.COPIED,
                InjectionStatus.QUEUED,
            }
        except Exception as exc:
            if claimed and delivery_id is not None:
                self._record_delivery_outcome(
                    delivery_id,
                    True,
                    "submitted_unconfirmed" if started_submission else "failed",
                    type(exc).__name__,
                )
            self.logger.warning("professional_insertion_failed type=%s", type(exc).__name__)
            with self._lock:
                self._error_message = "Не удалось отправить текст в целевое окно"
            return False
        finally:
            with self._lock:
                self._delivery_busy = False

    def read_professional_selection(self) -> SelectedText:
        with self._lock:
            if (
                self._delivery_busy
                or self._system_unavailable
                or self._shutdown_requested.is_set()
                or self.state
                not in {
                    AppState.IDLE,
                    AppState.COMPLETED,
                    AppState.ERROR_RECOVERABLE,
                }
            ):
                raise SelectionUnavailable("Сначала завершите активную диктовку")
            self._delivery_busy = True
        try:
            decision = self.target_manager.start_session()
            if (
                decision.target is None
                or self.policy.blocked(DictationContext.capture(decision.target, "selection"))
                or not (
                    decision.can_inject
                    or (
                        decision.foreground is not None
                        and decision.foreground.process_id == os.getpid()
                        and self.target_manager.try_restore_focus()
                    )
                )
            ):
                raise SelectionUnavailable("Не удалось вернуть фокус в исходный редактор")
            return self._selection_provider.read(decision.target)
        finally:
            with self._lock:
                self._delivery_busy = False

    def replace_professional_selection(self, selection: SelectedText, text: str) -> bool:
        with self._lock:
            if (
                self._delivery_busy
                or self._system_unavailable
                or self._shutdown_requested.is_set()
                or self.state
                not in {
                    AppState.IDLE,
                    AppState.COMPLETED,
                    AppState.ERROR_RECOVERABLE,
                }
            ):
                return False
            self._delivery_busy = True
        try:
            if self.policy.blocked(DictationContext.capture(selection.target, "selection")):
                return False
            foreground = get_foreground_hwnd()
            if not foreground or (
                foreground != selection.target.hwnd
                and get_window_process_id(foreground) != os.getpid()
            ):
                return False
            self.target_manager.set_locked_target(selection.target)
            if foreground != selection.target.hwnd and not self.target_manager.try_restore_focus():
                return False

            def verify_selection() -> bool:
                with self._lock:
                    if self._system_unavailable or self._shutdown_requested.is_set():
                        return False
                try:
                    current = self._selection_provider.read(selection.target)
                except SelectionUnavailable:
                    return False
                return current.text == selection.text and current.control_id == selection.control_id

            return self.injection_service.dispatch_explicit(
                text, selection.target, verify_selection=verify_selection
            )
        finally:
            with self._lock:
                self._delivery_busy = False

    def copy_pending_text(self) -> str:
        result = self.injection_service.copy_pending_to_clipboard(consume=True)
        return result.delivered_text

    def copy_previous_result(self, number: int | None = None) -> bool:
        with self._lock:
            if number is not None and not self.professional_session_is_current(number):
                return False
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

    def copy_pending_result(self) -> bool:
        with self._lock:
            if (
                self.state not in {AppState.COMPLETED, AppState.IDLE, AppState.ERROR_RECOVERABLE}
                or self._delivery_busy
            ):
                return False
            if (
                not isinstance(self.injection_service, TextInjectionService)
                or not self._pending_insertion_count
            ):
                return False
            self._delivery_busy = True
        try:
            result = self.injection_service.copy_pending_to_clipboard(consume=True)
            if result.status is not InjectionStatus.COPIED:
                return False
            with self._lock:
                self._delivery_status = "copied"
                self._target_reselect_required = False
                self._error_message = ""
                self._status_message = "Ожидающий текст скопирован для ручной вставки"
            self.target_manager.end_session()
            return True
        finally:
            with self._lock:
                self._delivery_busy = False

    def retry_completed_insertion(self, *, multiline_confirmed: bool = False) -> bool:
        """Reserve the completed session while dispatching its pending text."""
        with self._lock:
            if (
                self.state is not AppState.COMPLETED
                or self._delivery_busy
                or self._shutdown_requested.is_set()
                or not self._pending_insertion_count
            ):
                return False
            self._delivery_busy = True
        try:
            return self._retry_retained_insertion(multiline_confirmed=multiline_confirmed)
        finally:
            with self._lock:
                self._delivery_busy = False

    @property
    def input_caret(self) -> tuple[int, int, int, int] | None:
        context = self._input_context
        if context is None or not context.caret_screen:
            return None
        return context.caret_screen

    def current_caret(self) -> tuple[int, int, int, int] | None:
        saved = self._input_context
        if saved is None or get_foreground_hwnd() != saved.window_hwnd:
            return None
        target = self.target_manager.locked_target
        if target is None or target.identity != saved.identity:
            return None
        current = ForegroundContextProvider.native(target, saved.target_mode)
        if current.same_session and not current.is_password:
            return current.caret_screen
        return None

    def select_result_target(self) -> bool:
        with self._lock:
            if (
                self._system_unavailable
                or self.state not in {AppState.COMPLETED, AppState.IDLE}
                or self._delivery_busy
            ):
                return False
            self._delivery_busy = True
            previous = self.target_manager.locked_target
        try:
            target = self.target_manager.remember_foreground_target()
            if target is None:
                return False
            if isinstance(self.injection_service, TextInjectionService):
                context = DictationContext.capture(
                    target, self.settings.dictation.target_mode.value
                )
                profile = self.profiles.resolve(context)
                if not self.injection_service.bind_pending_to_target(
                    target,
                    rule=self.profiles.compatibility_rule(profile, context),
                ):
                    self.target_manager.set_locked_target(previous)
                    return False
            with self._lock:
                self._target_reselect_required = False
            return True
        finally:
            with self._lock:
                self._delivery_busy = False

    def clear_last_result(self) -> bool:
        with self._lock:
            if not self.can_change_runtime_settings() or self._pending_insertion_count:
                return False
            self._last_fragment = ""
            self._completed_transcript = ""
            self._previous_completed_text = ""
            self._last_processed = None
            self._recognition_result = None
            self.merger.reset()
            return True

    def _retry_retained_insertion(self, *, multiline_confirmed: bool = False) -> bool:
        if self._system_unavailable or self._target_reselect_required:
            with self._lock:
                self._error_message = "Выберите целевое окно заново или скопируйте текст"
            return False
        result = (
            self.injection_service.flush(retry=True, multiline_confirmed=multiline_confirmed)
            if isinstance(self.injection_service, TextInjectionService)
            else self.injection_service.flush()
        )
        if result.status is InjectionStatus.INSERTED:
            self._reconcile_outbox_retry()
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
                self._error_message = FAILURES[FailureCode.INSERTION_FAILED].text()
        return False

    def _finalize_session(self, status: str, *, requires_recovery: bool = False) -> None:
        with self._lock:
            self._cancel_duration_timer()
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
            journal = self._journal
            self._audio_archive = None
            stats = capture.stats() if capture is not None else self._last_stats
            completed_text = self.merger.committed_text
            live_command = self._live_command_resolution
            save_unsent_live = (
                self._live_session is not None and not self._live_text_committed and completed_text
            )
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
            draft_saved = (
                bool(completed_text.strip())
                and self._practice_language is None
                and self.settings.privacy.recover_unsaved_drafts
                and not self.settings.privacy.save_history
                and not self.policy.disable_history
            )
            if draft_saved:
                self.session_repository.save_draft(session_id, completed_text)
            if journal is not None:
                try:
                    if save_unsent_live:
                        journal.commit_text(completed_text)
                    event = "session_completed" if status == "completed" else "session_interrupted"
                    journal.append(event, {"status": status})
                except (OSError, RuntimeError, sqlite3.Error):
                    self.logger.warning("session_terminal_journal_failed")
            if requires_recovery:
                self.session_repository.require_recovery(session_id)
            else:
                self.session_repository.finish(session_id, status=status)
            try:
                self.usage_metrics.record(
                    self._session_profile.id,
                    status,
                    stats.processed_audio_seconds if stats is not None else 0.0,
                    len(completed_text.split()),
                    self._processing_seconds,
                )
            except Exception as exc:
                self.logger.warning("usage_metrics_write_failed type=%s", type(exc).__name__)
            if not requires_recovery and (
                self._practice_language is not None
                or (
                    spool is None
                    and not draft_saved
                    and (not self.settings.privacy.save_history or self.policy.disable_history)
                )
            ):
                self.session_repository.delete(session_id)
        # Retain the original window identity while committed fragments still
        # await delivery. Otherwise a completed session can never flush when
        # the user returns to that window.
        if (
            not (live_command and not self._live_command_claimed)
            and not self._pending_insertion_count
        ):
            self.target_manager.end_session()
        if audio_archive is not None:
            audio_archive.close()
        no_result = status == "completed" and not completed_text.strip() and live_command is None
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
            self._live_session = None
            self._live_transcript = None
            self._audio_queue = None
            self._spool = None
            self._segmenter = None
            self._journal = None
            self._completed_transcript = completed_text
            outcome = (
                RecognitionStatus.EMPTY
                if no_result
                else RecognitionStatus.COMPLETED
                if status == "completed"
                else RecognitionStatus.CANCELED
                if self._processing_cancelled.is_set() or status in {"cancelled", "canceled"}
                else RecognitionStatus.FAILED
            )
            self._recognition_result = RecognitionResult(
                self._professional_session_number,
                completed_text,
                " ".join(self._raw_asr_parts),
                self._worker_language,
                tuple(self._recognized_segments),
                outcome,
                FailureCode.ASR_FAILED.value if outcome is RecognitionStatus.FAILED else "",
                profile_id=self._session_profile.id,
            )
            self._raw_asr_parts.clear()
            self._recognized_segments.clear()
            if completed_text.strip() and status == "completed" and self._practice_language is None:
                self._previous_completed_text = completed_text
            self._completed_duration_s = max(0.0, stopped - started) if started is not None else 0.0
            self._session_started_monotonic = None
            self._session_stopped_monotonic = None
            self._capture_started_monotonic = None
            self._pipeline_thread = None
            self._pending_delivery_thread = None
            if no_result:
                self._delivery_status = "no_result"
                self._status_message = (
                    "Звук не поступил. Проверьте выбранный микрофон и разрешения Windows."
                    if capture_empty
                    else "Речь не распознана. Проверьте уровень микрофона и повторите диктовку."
                )
            elif self._session_interrupted:
                self._delivery_status = "copy_required" if completed_text.strip() else "no_result"
                self._status_message = (
                    "Сеанс прерван. Скопируйте текст или выберите новое целевое окно."
                )
            elif live_command is not None and not completed_text:
                self._delivery_status = "no_result"
                self._status_message = "Распознана голосовая команда"
            elif (
                not self.settings.dictation.auto_insert
                or self._practice_language is not None
                or self._local_only
            ):
                self._delivery_status = "pending"
            elif self._delivery_status == "pending" and self._pending_insertion_count:
                self._delivery_status = "queued_target_inactive"
            if status == "completed" and completed_text and self._delivery_status == "pending":
                self._status_message = "Результат готов"
            self._user_paused = False
        if (
            status == "completed"
            and completed_text.strip()
            and not self._session_interrupted
            and not self._system_unavailable
            and not self._local_only
            and self._practice_language is None
        ):
            self.workflows.dispatch("dictation", self._session_profile.id, completed_text)
        if self.state is AppState.FINISHING:
            self.state_machine.transition(AppState.COMPLETED, "session drained and committed")
            if (
                status == "completed"
                and not self._system_unavailable
                and not self._session_interrupted
                and not self._local_only
                and self.settings.dictation.copy_full_text_on_complete
                and self.policy.allow_clipboard_persistence
                and completed_text
                and self._practice_language is None
            ):
                copier = getattr(self.injection_service, "copy_text_to_clipboard", None)
                copied = bool(copier(completed_text)) if callable(copier) else False
                with self._lock:
                    if copied:
                        self._status_message = "Готово — весь текст скопирован"
                    else:
                        self._error_message = "Текст сохранён, но буфер обмена недоступен"

    def begin_shutdown(self) -> None:
        """Close the admission gate without waiting for a worker."""
        self._shutdown_requested.set()
        self._start_cancelled.set()
        self._file_recognition_cancel.set()

    def shutdown(self) -> bool:
        self.begin_shutdown()
        if self._shutdown_complete:
            return True
        deadline = time.monotonic() + 2.0
        with self._lock:
            if self._model_switch_active or self._audio_monitor_active:
                return False
            if self._file_recognition_active:
                self._file_recognition_cancel.set()
                return False
            if self._recovery_active:
                self.logger.debug("shutdown_deferred recovery_still_running=true")
                return False
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
            pauser = self._pause_thread
        for worker in (starter, preloader, pauser):
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
                if worker.is_alive():
                    self.logger.error("shutdown_deferred worker_still_running=%s", worker.name)
                    return False
        if stopper is not None and stopper is not threading.current_thread():
            stopper.join(timeout=max(0.0, deadline - time.monotonic()))
            if stopper.is_alive():
                self.logger.error("shutdown_deferred worker_still_running=%s", stopper.name)
                return False
        with self._lock:
            finisher = self._finish_thread
        if finisher is not None and finisher is not threading.current_thread():
            finisher.join(timeout=max(0.0, deadline - time.monotonic()))
            if finisher.is_alive():
                self.logger.error("shutdown_deferred worker_still_running=%s", finisher.name)
                return False
        pipeline_alive = False
        with self._lock:
            pipeline_alive = self._pipeline_thread is not None and self._pipeline_thread.is_alive()
            if self._delivery_busy:
                return False
            if self._inference_thread is not None:
                self.logger.debug("shutdown_deferred native_inference_still_running=true")
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
        if self._delivery_thread.is_alive() and not self._delivery_stop_sent:
            try:
                self._delivery_queue.put_nowait(None)
            except Full:
                return False
            self._delivery_stop_sent = True
        if self._delivery_thread is not threading.current_thread():
            self._delivery_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if self._delivery_thread.is_alive():
            self.logger.error("shutdown_deferred delivery_worker_alive=true")
            return False
        self.injection_service.close()
        if self._file_backend is not None:
            self._file_backend.unload()
            self._file_backend = None
        self._shutdown_complete = True
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
