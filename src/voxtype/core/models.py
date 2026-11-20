"""Shared immutable domain models without UI or backend dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Language(StrEnum):
    RU = "ru"
    EN = "en"

    def toggled(self) -> Language:
        return Language.EN if self is Language.RU else Language.RU


class ModelProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    MAXIMUM_ACCURACY = "maximum_accuracy"


class TargetMode(StrEnum):
    LOCKED = "locked"
    FOLLOW_FOCUS = "follow_focus"


class InjectionMode(StrEnum):
    AUTO = "auto"
    CLIPBOARD_PASTE = "clipboard_paste"
    UNICODE_INPUT = "unicode_input"
    SAFE_CLIPBOARD = "safe_clipboard"


class PreprocessingProfile(StrEnum):
    RAW = "raw"
    NORMAL = "normal"
    NOISY_ROOM = "noisy_room"


@dataclass(frozen=True, slots=True)
class WordStamp:
    text: str
    start_s: float
    end_s: float
    probability: float = 1.0

    def shifted(self, seconds: float) -> WordStamp:
        return WordStamp(self.text, self.start_s + seconds, self.end_s + seconds, self.probability)


@dataclass(frozen=True, slots=True)
class TranscriptFragment:
    sequence: int
    text: str
    words: tuple[WordStamp, ...]
    chunk_start_s: float
    chunk_end_s: float
    language: Language
    average_log_probability: float = 0.0
    compression_ratio: float = 1.0
    no_speech_probability: float = 0.0
    inference_s: float = 0.0

    @property
    def audio_duration_s(self) -> float:
        return max(0.0, self.chunk_end_s - self.chunk_start_s)

    @property
    def rtf(self) -> float:
        duration = self.audio_duration_s
        return self.inference_s / duration if duration else 0.0


@dataclass(frozen=True, slots=True)
class AudioPacket:
    sequence: int
    captured_at: float
    sample_rate: int
    frames: int
    samples: Any
    spool_path: Path | None = None

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate > 0 else 0.0


@dataclass(frozen=True, slots=True)
class TargetInfo:
    hwnd: int
    title: str
    process_name: str
    process_id: int
    integrity_level: int | None
    captured_at: float
    is_own_window: bool = False


@dataclass(frozen=True, slots=True)
class HardwareInfo:
    os_name: str
    cpu_name: str
    physical_cores: int
    logical_cores: int
    total_ram_bytes: int
    available_ram_bytes: int
    gpu_names: tuple[str, ...] = field(default_factory=tuple)
    cuda_available: bool = False
    vulkan_available: bool = False
    input_devices: tuple[str, ...] = field(default_factory=tuple)
    supported_sample_rates: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AppReadiness:
    phase: AppPhase
    reason: str
    message: str
    can_record: bool
    can_cancel: bool
    session_id: int | None = None


class AppPhase(StrEnum):
    INITIALIZING = "initializing"
    MODEL_REQUIRED = "model_required"
    MODEL_LOADING = "model_loading"
    MICROPHONE_UNAVAILABLE = "microphone_unavailable"
    READY = "ready"
    RECORDING = "recording"
    PROCESSING = "processing"
    INSERTING = "inserting"
    ERROR = "error"
    SHUTTING_DOWN = "shutting_down"


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    state: str
    language: Language
    session_elapsed_s: float
    queued_audio_seconds: float
    last_committed_text: str
    provisional_text: str
    target_title: str
    input_rms: float
    clipping_percentage: float
    warning: str = ""
    microphone_name: str = "Системный микрофон"
    model_name: str = "Модель не загружена"
    model_profile: ModelProfile = ModelProfile.BALANCED
    status_message: str = ""
    error_message: str = ""
    full_transcript: str = ""
    delivery_status: str = "pending"
    readiness: AppReadiness | None = None
