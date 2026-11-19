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
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.request import Request, urlopen

from platformdirs import user_cache_path, user_data_path

from voxtype.asr.faster_whisper_backend import FasterWhisperBackend
from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS, ModelManager
from voxtype.asr.profiles import ASRProfile, ASRProfileConfig, get_profile
from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.devices import (
    InputDevice,
    choose_capture_channels,
    choose_capture_rate,
    list_input_devices,
)
from voxtype.audio.preprocessing import (
    PreprocessingProfile as AudioPreprocessingProfile,
)
from voxtype.audio.preprocessing import PreprocessorSettings
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.constants import APP_AUTHOR, APP_NAME
from voxtype.core.controller import ApplicationController
from voxtype.core.models import ModelProfile
from voxtype.monitoring.logging import configure_logging
from voxtype.sessions.repository import SessionRepository
from voxtype.sessions.retention import cleanup_retention
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings
from voxtype.vad.segmenter import SegmentationSettings, StreamingSegmenter
from voxtype.vad.silero import SileroVAD
from voxtype.version import __version__
from voxtype.windows.injection import InjectionStrategy, TextInjectionService
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


def configured_profile(settings: AppSettings) -> ASRProfileConfig:
    profile = get_profile(asr_profile(settings.dictation.model_profile))
    threads = effective_cpu_threads(settings)
    return replace(profile, cpu_threads=threads)


def effective_cpu_threads(settings: AppSettings) -> int:
    configured = settings.model.cpu_threads
    logical = os.cpu_count() or 2
    requested = max(1, min(configured, logical)) if configured > 0 else max(1, min(8, logical - 1))
    mode = settings.model.power_mode.strip().casefold()
    if mode in {"energy", "energy_saving", "eco"}:
        return max(1, min(requested, 4, logical // 2 or 1))
    if mode in {"maximum", "performance", "max_performance"}:
        return max(requested, min(12, logical))
    return requested


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


def install_silero_vad(data_directory: Path) -> Path:
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

    def backend_for(self, profile: ASRProfile | str) -> FasterWhisperBackend:
        selected = asr_profile(domain_profile(profile))
        config = get_profile(selected)
        threads = effective_cpu_threads(self.settings)
        config = replace(config, cpu_threads=threads)
        path = self.model_manager.path_for(config.model_name)
        return FasterWhisperBackend(path, config)

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
        )

    def close(self) -> bool:
        if not self.controller.shutdown():
            return False
        self.controller.backend.unload()
        retry_backend = self.controller.retry_backend
        if retry_backend is not None and retry_backend is not self.controller.backend:
            retry_backend.unload()
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
    session_repository = (
        SessionRepository(data_directory / "sessions.sqlite3")
        if isolated_profile
        else SessionRepository()
    )
    stage("session_repository_open")
    cleanup_retention(
        session_repository,
        data_directory / "audio",
        settings.privacy.retention_days,
        spool_root=cache_directory / "audio-spool",
    )
    stage("retention_checked")
    model_manager = ModelManager(data_directory / "models")
    devices = list_input_devices()
    device = select_input_device(devices, settings.audio.device_id)
    stage("audio_devices_enumerated")

    profile = configured_profile(settings)
    if settings.model.installed_model != profile.model_name:
        settings.model.installed_model = profile.model_name
        settings_repository.save(settings)
    backend = FasterWhisperBackend(model_manager.path_for(profile.model_name), profile)
    retry_backend: FasterWhisperBackend | None = None
    if profile.profile is ASRProfile.BALANCED and model_manager.is_installed("large-v3-turbo"):
        retry_profile = replace(
            get_profile(ASRProfile.MAXIMUM_ACCURACY),
            cpu_threads=effective_cpu_threads(settings),
        )
        retry_backend = FasterWhisperBackend(
            model_manager.path_for(retry_profile.model_name),
            retry_profile,
        )
    target_manager = TargetWindowManager(policy=TargetPolicy(settings.dictation.target_mode.value))
    injection = TextInjectionService(
        target_manager,
        strategy=InjectionStrategy(settings.dictation.injection_mode.value),
        paste_delay=max(350, settings.dictation.insertion_delay_ms) / 1_000.0,
    )

    audio_profile = AudioPreprocessingProfile(settings.audio.preprocessing_profile.value)
    preprocessing = replace(
        PreprocessorSettings.for_profile(audio_profile),
        normalization_enabled=settings.audio.normalization,
        noise_suppression_enabled=settings.audio.noise_suppression,
    )
    capture_rate = choose_capture_rate(device)

    def capture_factory(queue: LosslessAudioQueue) -> AudioCaptureService:
        return AudioCaptureService(
            queue,
            AudioCaptureConfig(
                # Keep an explicitly selected microphone pinned, but ask
                # PortAudio for the current Windows default at each session
                # when the user left the device on "default".
                device=device.index if settings.audio.device_id is not None else None,
                input_sample_rate=capture_rate,
                input_channels=choose_capture_channels(device),
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
        spool_root=cache_directory / "audio-spool",
        microphone_name=f"{device.name} ({device.host_api})",
    )
    controller.initialize(preload_model=False)
    stage("controller_initialized")
    return RuntimeBundle(
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
        model_manager.is_installed(profile.model_name),
    )


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
