"""Composition root for the local VoxType runtime.

The module deliberately has no Qt dependency, which keeps diagnostics and
packaging self-tests usable on a headless Windows session.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.request import Request, urlopen

from platformdirs import user_cache_path, user_data_path

from voxtype.asr.base import ASRModelUnavailable
from voxtype.asr.faster_whisper_backend import FasterWhisperBackend, WhisperEngineSlot
from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS, ModelManager
from voxtype.asr.profiles import ASRProfile, ASRProfileConfig, get_profile
from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.devices import (
    AudioBackendUnavailable,
    InputDevice,
    choose_capture_channels,
    choose_capture_rate,
    list_input_devices,
    match_input_device,
    resolve_recording_device,
)
from voxtype.audio.preprocessing import (
    PreprocessingProfile as AudioPreprocessingProfile,
)
from voxtype.audio.preprocessing import select_preprocessing
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.constants import APP_AUTHOR, APP_NAME
from voxtype.core.compute_budget import ComputeBudget, DecodingPreset
from voxtype.core.controller import ApplicationController
from voxtype.core.models import ModelProfile
from voxtype.monitoring.logging import configure_logging
from voxtype.sessions.backup import BackupManager
from voxtype.sessions.repository import SessionRepository
from voxtype.sessions.retention import cleanup_retention
from voxtype.settings.enterprise import EnterprisePolicyService
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings
from voxtype.vad.segmenter import SegmentationSettings, StreamingSegmenter
from voxtype.vad.silero import SileroVAD
from voxtype.version import __version__
from voxtype.windows.injection import InjectionStrategy, TextInjectionService
from voxtype.windows.input_context import ForegroundContextProvider
from voxtype.windows.startup import MemoryStartupRegistryBackend, StartupManager
from voxtype.windows.target_window import TargetPolicy, TargetWindowManager

SILERO_VAD_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    "76e3dc408eb2a5c655c34e230d2d5459b4439daa/src/silero_vad/data/silero_vad.onnx"
)
SILERO_VAD_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


def asr_profile(profile: ModelProfile | str) -> ASRProfile:
    """Translate the persisted domain profile to the ASR implementation enum."""

    value = profile.value if isinstance(profile, ModelProfile) else str(profile)
    if value == ModelProfile.MAXIMUM_ACCURACY.value:
        return ASRProfile.MAXIMUM_ACCURACY
    return ASRProfile(value)


def domain_profile(profile: ASRProfile | str) -> ModelProfile:
    value = profile.value if isinstance(profile, ASRProfile) else str(profile)
    if value == ASRProfile.MAXIMUM_ACCURACY.value or value == "accurate":
        return ModelProfile.MAXIMUM_ACCURACY
    return ModelProfile(value)


def configured_profile(
    settings: AppSettings,
    selection: ASRProfile | None = None,
) -> ASRProfileConfig:
    profile = get_profile(selection or asr_profile(settings.dictation.model_profile))
    profile = replace(profile, cpu_threads=effective_cpu_threads(settings))
    preset = settings.model.decoding_preset
    if preset == DecodingPreset.FAST:
        return replace(profile, beam_size=1, best_of=1)
    if preset == DecodingPreset.ACCURATE:
        return replace(profile, beam_size=max(3, profile.beam_size))
    return profile


def effective_cpu_threads(settings: AppSettings) -> int:
    return ComputeBudget.from_settings(
        settings.model.power_mode, settings.model.cpu_threads
    ).asr_intra_threads


def select_input_device(
    devices: tuple[InputDevice, ...], selected_index: int | None
) -> InputDevice:
    """Prefer the configured device, then default WASAPI, then any default/input."""

    if selected_index is not None:
        selected = next((item for item in devices if item.index == selected_index), None)
        if selected is not None:
            return selected
    ranked = sorted(
        devices,
        key=lambda item: (
            "wasapi" not in item.host_api.casefold(),
            not item.is_default,
            item.index,
        ),
    )
    if not ranked:
        raise RuntimeError("Windows не обнаружила доступный микрофон.")
    return ranked[0]


def install_silero_vad(data_directory: Path, *, allow_download: bool = True) -> Path | None:
    """Install the pinned official ONNX VAD atomically during explicit setup."""

    destination = data_directory / "models" / "silero-vad" / "silero_vad.onnx"
    if destination.is_file() and _sha256(destination) == SILERO_VAD_SHA256:
        return destination
    bundled = Path(sys.executable).resolve().parent / "voxtype_assets" / "silero_vad.onnx"
    if bundled.is_file() and _sha256(bundled) == SILERO_VAD_SHA256:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.bundled")
        shutil.copy2(bundled, temporary)
        os.replace(temporary, destination)
        return destination
    if not allow_download:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".silero-vad-", suffix=".onnx", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        request = Request(SILERO_VAD_URL, headers={"User-Agent": "VoxType/1"})
        digest = hashlib.sha256()
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
                digest.update(block)
        if digest.hexdigest() != SILERO_VAD_SHA256:
            raise RuntimeError("Контрольная сумма Silero VAD не совпадает.")
        from onnxruntime import InferenceSession

        session = InferenceSession(str(temporary), providers=["CPUExecutionProvider"])
        inputs = {item.name.casefold() for item in session.get_inputs()}
        if not ({"input", "state", "sr"} <= inputs):
            raise RuntimeError("Silero VAD имеет несовместимую ONNX-сигнатуру.")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def data_directory_path() -> Path:
    """Return the production data root, with an explicit acceptance-test override."""

    override = os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip()
    return Path(override).resolve() if override else user_data_path(APP_NAME, APP_AUTHOR)


def cache_directory_path() -> Path:
    """Return the cache root without coupling tests to Windows Known Folder APIs."""

    override = os.environ.get("VOXTYPE_CACHE_DIRECTORY", "").strip()
    if override:
        return Path(override).resolve()
    if os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip():
        return data_directory_path() / "cache"
    return user_cache_path(APP_NAME, APP_AUTHOR)


@dataclass(slots=True)
class RuntimeBundle:
    settings_repository: SettingsRepository
    session_repository: SessionRepository
    model_manager: ModelManager
    settings: AppSettings
    devices: tuple[InputDevice, ...]
    device: InputDevice
    controller: ApplicationController
    startup: StartupManager
    data_directory: Path
    log_path: Path
    selected_model_installed: bool
    engine_slot: WhisperEngineSlot | None = None
    restore_status: str = ""

    @property
    def secret_store(self):
        from voxtype.windows.secret_store import WindowsSecretStore

        return WindowsSecretStore(self.data_directory / "credentials")

    def backend_for(self, profile: ASRProfile | str) -> FasterWhisperBackend:
        selected = asr_profile(domain_profile(profile))
        config = configured_profile(self.settings, selected)
        path = self.model_manager.path_for(config.model_name)
        return FasterWhisperBackend(path, config, engine_slot=self.engine_slot)

    def retry_backend_for(self, profile: ASRProfile | str) -> FasterWhisperBackend | None:
        selected = asr_profile(domain_profile(profile))
        if selected is not ASRProfile.BALANCED or not self.model_manager.is_installed(
            "large-v3-turbo"
        ):
            return None
        retry_config = replace(
            get_profile(ASRProfile.MAXIMUM_ACCURACY),
            cpu_threads=effective_cpu_threads(self.settings),
        )
        return FasterWhisperBackend(
            self.model_manager.path_for(retry_config.model_name),
            retry_config,
            engine_slot=self.engine_slot,
        )

    def backend_for_model(self, model_id: str) -> FasterWhisperBackend:
        if not self.model_manager.is_installed(model_id):
            raise ASRModelUnavailable("Selected profile model is not installed")
        profile = (
            ASRProfile.MAXIMUM_ACCURACY if model_id == "large-v3-turbo" else ASRProfile.BALANCED
        )
        return self.backend_for(profile)

    def close(self) -> bool:
        if not self.controller.shutdown():
            return False
        if self.engine_slot is not None:
            with self.engine_slot.lock:
                self.engine_slot.clear()
        else:
            self.controller.backend.unload()
            retry_backend = self.controller.retry_backend
            if retry_backend is not None and retry_backend is not self.controller.backend:
                retry_backend.unload()
        SileroVAD.release_cached_session()
        self.session_repository.close()
        return True


def build_runtime(*, verbose: bool = False) -> RuntimeBundle:
    """Create one production runtime without starting capture or model download."""

    startup_started = time.perf_counter()
    previous_stage = startup_started

    def stage(name: str) -> None:
        nonlocal previous_stage
        now = time.perf_counter()
        logging.getLogger("voxtype.startup").info(
            "startup_stage name=%s duration_ms=%.1f total_ms=%.1f",
            name,
            (now - previous_stage) * 1_000.0,
            (now - startup_started) * 1_000.0,
        )
        previous_stage = now

    data_directory = data_directory_path()
    cache_directory = cache_directory_path()
    isolated_profile = bool(os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip())
    data_directory.mkdir(parents=True, exist_ok=True)
    cache_directory.mkdir(parents=True, exist_ok=True)
    log_path = configure_logging(
        data_directory / "logs" if isolated_profile else None,
        verbose=verbose,
    )
    logging.getLogger("voxtype.runtime").info(
        "application_start version=%s python=%s os=%s",
        __version__,
        platform.python_version(),
        platform.platform(),
    )
    stage("logging_ready")
    settings_repository = (
        SettingsRepository(data_directory / "settings.json")
        if isolated_profile
        else SettingsRepository()
    )
    settings = settings_repository.load()
    stage("settings_loaded")
    database_path = data_directory / "sessions.sqlite3"
    backups = BackupManager(database_path, data_directory / "backups")
    restore_status = ""
    try:
        if backups.apply_pending_restore():
            restore_status = "restored"
            logging.getLogger(__name__).info("confirmed_database_restore_applied")
    except (OSError, ValueError, sqlite3.Error) as exc:
        restore_status = "failed"
        logging.getLogger(__name__).warning("database_restore_failed kind=%s", type(exc).__name__)
        if not database_path.exists():
            raise
    session_repository = SessionRepository(database_path)
    stage("session_repository_open")
    cleanup_retention(
        session_repository,
        data_directory / "audio",
        settings.privacy.retention_days,
        spool_root=cache_directory / "audio-spool",
    )
    stage("retention_checked")
    model_manager = ModelManager(data_directory / "models")
    try:
        devices = list_input_devices()
    except AudioBackendUnavailable:
        logging.getLogger("voxtype.runtime").warning("audio_device_discovery_unavailable")
        devices = ()
    matched_device = match_input_device(
        devices,
        settings.audio.device_id,
        settings.audio.device_fingerprint,
        settings.audio.device_endpoint_id if settings.audio.device_id is not None else "",
    )
    device = matched_device or InputDevice(-1, "Микрофон недоступен", "", 0, 0, (), False)
    stage("audio_devices_enumerated")

    profile = configured_profile(settings)
    if settings.model.installed_model != profile.model_name:
        settings.model.installed_model = profile.model_name
        settings_repository.save(settings)
    engine_slot = WhisperEngineSlot()
    backend = FasterWhisperBackend(
        model_manager.path_for(profile.model_name),
        profile,
        engine_slot=engine_slot,
    )
    retry_backend: FasterWhisperBackend | None = None
    if profile.profile is ASRProfile.BALANCED and model_manager.is_installed("large-v3-turbo"):
        retry_profile = replace(
            get_profile(ASRProfile.MAXIMUM_ACCURACY),
            cpu_threads=effective_cpu_threads(settings),
        )
        retry_backend = FasterWhisperBackend(
            model_manager.path_for(retry_profile.model_name),
            retry_profile,
            engine_slot=engine_slot,
        )
    target_manager = TargetWindowManager(policy=TargetPolicy(settings.dictation.target_mode.value))
    enterprise = EnterprisePolicyService()
    injection = TextInjectionService(
        target_manager,
        strategy=InjectionStrategy(settings.dictation.injection_mode.value),
        paste_delay=max(350, settings.dictation.insertion_delay_ms) / 1_000.0,
        persist_pending=(
            settings.privacy.save_history
            and not enterprise.policy.disable_history
            and enterprise.policy.allow_clipboard_persistence
        ),
        context_provider=ForegroundContextProvider() if os.name == "nt" else None,
    )

    audio_profile = AudioPreprocessingProfile(settings.audio.preprocessing_profile.value)
    preprocessing = select_preprocessing(
        settings.audio.preprocessing_mode,
        audio_profile,
        normalize=settings.audio.normalization,
        noise_gate=settings.audio.noise_suppression,
    )

    def capture_factory(queue: LosslessAudioQueue) -> AudioCaptureService:
        current = (
            list_input_devices(candidate_sample_rates=(16_000, 48_000))
            if settings.audio.device_id is not None
            else ()
        )
        selected = resolve_recording_device(
            settings.audio.device_id,
            current,
            settings.audio.device_fingerprint,
            settings.audio.device_endpoint_id if settings.audio.device_id is not None else "",
        )
        return AudioCaptureService(
            queue,
            AudioCaptureConfig(
                device=selected.index,
                input_sample_rate=choose_capture_rate(selected),
                input_channels=choose_capture_channels(selected),
                callback_queue_chunks=2048,
            ),
            preprocessing=preprocessing,
        )

    def segmenter_factory() -> StreamingSegmenter:
        model_path = data_directory / "models" / "silero-vad" / "silero_vad.onnx"
        detector = SileroVAD(model_path, strict=False)
        base = SegmentationSettings()
        threshold = min(0.75, max(0.25, 0.70 - settings.audio.vad_sensitivity * 0.5))
        return StreamingSegmenter(detector, settings=replace(base, speech_threshold=threshold))

    controller = ApplicationController(
        settings_repository=settings_repository,
        session_repository=session_repository,
        backend=backend,
        retry_backend=retry_backend,
        capture_factory=capture_factory,
        segmenter_factory=segmenter_factory,
        target_manager=target_manager,
        injection_service=injection,
        enterprise=enterprise,
        spool_root=cache_directory / "audio-spool",
        microphone_name=f"{device.name} ({device.host_api})",
    )
    installed = model_manager.is_installed(profile.model_name)
    controller.set_recording_availability(
        microphone=matched_device is not None,
        model=installed,
    )
    controller.initialize(preload_model=False)
    stage("controller_initialized")
    bundle = RuntimeBundle(
        settings_repository,
        session_repository,
        model_manager,
        settings,
        devices,
        device,
        controller,
        StartupManager(backend=MemoryStartupRegistryBackend() if isolated_profile else None),
        data_directory,
        log_path,
        installed,
        engine_slot,
        restore_status,
    )
    controller.profile_backend_factory = bundle.backend_for_model
    return bundle


__all__ = [
    "DEFAULT_MODEL_SPECS",
    "RuntimeBundle",
    "asr_profile",
    "build_runtime",
    "cache_directory_path",
    "configured_profile",
    "data_directory_path",
    "domain_profile",
    "effective_cpu_threads",
    "install_silero_vad",
    "select_input_device",
]
