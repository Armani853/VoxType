"""Typed, Qt-independent view models used by the VoxType user interface.

The controller deliberately does not depend on Qt.  ``snapshot_from`` is the
small adapter between controller snapshots (objects or mappings) and widgets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from voxtype.core.models import AppReadiness


class AppStatus(StrEnum):
    STARTING = "STARTING"
    MODEL_LOADING = "MODEL_LOADING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    SPEECH_DETECTED = "SPEECH_DETECTED"
    TRANSCRIBING = "TRANSCRIBING"
    PAUSED_TARGET_LOST = "PAUSED_TARGET_LOST"
    FINISHING = "FINISHING"
    COMPLETED = "COMPLETED"
    ERROR_RECOVERABLE = "ERROR_RECOVERABLE"
    ERROR_FATAL = "ERROR_FATAL"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    PAUSED = "PAUSED"

    @classmethod
    def coerce(cls, value: object) -> AppStatus:
        """Convert core enum/string values without importing the core package."""

        if isinstance(value, cls):
            return value
        candidate = getattr(value, "name", value)
        normalized = str(candidate or cls.IDLE).upper().replace(" ", "_")
        aliases = {
            "SPEECH": cls.SPEECH_DETECTED,
            "PROCESSING": cls.TRANSCRIBING,
            "WARNING": cls.ERROR_RECOVERABLE,
            "ERROR": cls.ERROR_RECOVERABLE,
            "READY": cls.IDLE,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError:
            return cls.ERROR_RECOVERABLE

    @property
    def active(self) -> bool:
        return self in {
            self.STARTING,
            self.MODEL_LOADING,
            self.LISTENING,
            self.SPEECH_DETECTED,
            self.TRANSCRIBING,
            self.PAUSED_TARGET_LOST,
            self.FINISHING,
            self.PAUSED,
        }


class DictationLanguage(StrEnum):
    RU = "ru"
    EN = "en"

    @classmethod
    def coerce(cls, value: object) -> DictationLanguage:
        if isinstance(value, cls):
            return value
        candidate = getattr(value, "value", value)
        normalized = str(candidate or "ru").strip().lower()
        return cls.EN if normalized.startswith("en") else cls.RU


class ModelProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURATE = "accurate"

    @classmethod
    def coerce(cls, value: object) -> ModelProfile:
        if isinstance(value, cls):
            return value
        candidate = getattr(value, "value", value)
        normalized = str(candidate or "balanced").strip().lower()
        aliases = {
            "quick": cls.FAST,
            "быстро": cls.FAST,
            "balance": cls.BALANCED,
            "баланс": cls.BALANCED,
            "maximum": cls.ACCURATE,
            "max_accuracy": cls.ACCURATE,
            "maximum_accuracy": cls.ACCURATE,
            "максимальная точность": cls.ACCURATE,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError:
            return cls.BALANCED


@dataclass(slots=True)
class UIState:
    """A complete immutable-by-convention snapshot rendered by every UI view."""

    state: AppStatus = AppStatus.IDLE
    language: DictationLanguage = DictationLanguage.RU
    session_elapsed_s: float = 0.0
    queued_audio_seconds: float = 0.0
    last_committed_text: str = ""
    provisional_text: str = ""
    target_title: str = ""
    input_rms: float = 0.0
    clipping_percentage: float = 0.0
    microphone_name: str = "Системный микрофон"
    model_name: str = "Модель не загружена"
    model_profile: ModelProfile = ModelProfile.BALANCED
    status_message: str = ""
    error_message: str = ""
    full_transcript: str = ""
    delivery_status: str = "pending"
    readiness: AppReadiness | None = None

    def __post_init__(self) -> None:
        self.state = AppStatus.coerce(self.state)
        self.language = DictationLanguage.coerce(self.language)
        self.model_profile = ModelProfile.coerce(self.model_profile)
        self.session_elapsed_s = max(0.0, _finite_float(self.session_elapsed_s))
        self.queued_audio_seconds = max(0.0, _finite_float(self.queued_audio_seconds))
        self.input_rms = min(1.0, max(0.0, _finite_float(self.input_rms)))
        self.clipping_percentage = min(100.0, max(0.0, _finite_float(self.clipping_percentage)))


@dataclass(slots=True)
class HistoryEntry:
    session_id: str
    started_at: datetime | str | float
    ended_at: datetime | str | float | None = None
    language: DictationLanguage = DictationLanguage.RU
    model: str = ""
    profile: ModelProfile = ModelProfile.BALANCED
    target_application: str = ""
    confirmed_text: str = ""
    warnings: tuple[str, ...] = ()
    suspicious_fragments: tuple[str, ...] = ()
    duration_s: float = 0.0
    processing_time_s: float = 0.0
    dropped_frames: int = 0
    completion_status: str = "completed"
    profile_id: str = "general"
    source_type: str = "dictation"

    def __post_init__(self) -> None:
        self.language = DictationLanguage.coerce(self.language)
        self.profile = ModelProfile.coerce(self.profile)
        self.warnings = tuple(self.warnings)
        self.suspicious_fragments = tuple(self.suspicious_fragments)
        self.duration_s = max(0.0, _finite_float(self.duration_s))
        self.processing_time_s = max(0.0, _finite_float(self.processing_time_s))
        self.dropped_frames = max(0, int(self.dropped_frames))


@dataclass(slots=True)
class DiagnosticReport:
    summary: str = "Диагностика ещё не выполнялась"
    hardware: dict[str, str] = field(default_factory=dict)
    audio: dict[str, str] = field(default_factory=dict)
    runtime: dict[str, str] = field(default_factory=dict)
    libraries: dict[str, str] = field(default_factory=dict)
    checks: tuple[tuple[str, bool, str], ...] = ()
    log_excerpt: str = ""

    def __post_init__(self) -> None:
        self.hardware = _string_dict(self.hardware)
        self.audio = _string_dict(self.audio)
        self.runtime = _string_dict(self.runtime)
        self.libraries = _string_dict(self.libraries)
        self.checks = tuple((str(name), bool(ok), str(detail)) for name, ok, detail in self.checks)


def snapshot_from(source: UIState | Mapping[str, Any] | object) -> UIState:
    """Adapt a controller or mapping to :class:`UIState` defensively.

    Unknown fields are ignored.  This makes UI polling resilient while the
    controller adds metrics over time and avoids a dependency in either
    direction.
    """

    if isinstance(source, UIState):
        return source

    def read(name: str, default: Any) -> Any:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)

    return UIState(
        state=read("state", AppStatus.IDLE),
        language=read("language", DictationLanguage.RU),
        session_elapsed_s=read("session_elapsed_s", 0.0),
        queued_audio_seconds=read("queued_audio_seconds", 0.0),
        last_committed_text=read("last_committed_text", ""),
        provisional_text=read("provisional_text", ""),
        target_title=read("target_title", ""),
        input_rms=read("input_rms", 0.0),
        clipping_percentage=read("clipping_percentage", 0.0),
        microphone_name=read("microphone_name", read("microphone", "Системный микрофон")),
        model_name=read("model_name", read("model", "Модель не загружена")),
        model_profile=read("model_profile", read("profile", ModelProfile.BALANCED)),
        status_message=read("status_message", read("warning", "")),
        error_message=read("error_message", ""),
        full_transcript=read("full_transcript", ""),
        delivery_status=read("delivery_status", "pending"),
        readiness=read("readiness", None),
    )


def history_entry_from(source: HistoryEntry | Mapping[str, Any] | object) -> HistoryEntry:
    """Adapt session repository rows without coupling the UI to its storage type."""

    if isinstance(source, HistoryEntry):
        return source

    def read(name: str, default: Any) -> Any:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)

    return HistoryEntry(
        session_id=str(read("session_id", read("id", ""))),
        started_at=read("started_at", ""),
        ended_at=read("ended_at", read("finished_at", None)),
        language=read("language", "ru"),
        model=str(read("model", "")),
        profile=read("profile", "balanced"),
        target_application=str(read("target_application", read("target_title", ""))),
        confirmed_text=str(read("confirmed_text", read("committed_text", read("text", "")))),
        warnings=tuple(read("warnings", ()) or ()),
        suspicious_fragments=tuple(read("suspicious_fragments", ()) or ()),
        duration_s=read("duration_s", read("audio_duration_s", read("duration", 0.0))),
        processing_time_s=read("processing_time_s", read("processing_s", 0.0)),
        dropped_frames=read("dropped_frames", 0),
        completion_status=str(read("completion_status", read("status", "completed"))),
        profile_id=str(read("profile_id", "general")),
        source_type=str(read("source_type", "dictation")),
    )


def diagnostic_report_from(
    source: DiagnosticReport | Mapping[str, Any] | object,
) -> DiagnosticReport:
    if isinstance(source, DiagnosticReport):
        return source

    def read(name: str, default: Any) -> Any:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)

    return DiagnosticReport(
        summary=str(read("summary", "Диагностика ещё не выполнялась")),
        hardware=dict(read("hardware", {}) or {}),
        audio=dict(read("audio", {}) or {}),
        runtime=dict(read("runtime", {}) or {}),
        libraries=dict(read("libraries", {}) or {}),
        checks=tuple(read("checks", ()) or ()),
        log_excerpt=str(read("log_excerpt", "")),
    )


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_backlog(seconds: float) -> str:
    value = max(0.0, _finite_float(seconds))
    return f"{value:.1f} с" if value < 60 else f"{value / 60:.1f} мин"


def text_preview(text: str, max_characters: int = 700) -> str:
    """Bound long-session text rendered by frequently refreshed widgets."""

    limit = max(32, int(max_characters))
    if len(text) <= limit:
        return text
    tail = text[-(limit - 1) :]
    first_space = tail.find(" ")
    if 0 <= first_space < len(tail) // 4:
        tail = tail[first_space + 1 :]
    return f"…{tail}"


def _finite_float(value: object) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return 0.0
    if result != result or result in {float("inf"), float("-inf")}:
        return 0.0
    return result


def _string_dict(value: Mapping[Any, Any]) -> dict[str, str]:
    return {str(key): str(item) for key, item in value.items()}
