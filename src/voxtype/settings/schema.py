"""Dataclass settings schema with explicit enum conversion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from voxtype.constants import (
    DEFAULT_HOTKEY,
    DEFAULT_LANGUAGE_HOTKEY,
    DEFAULT_PAUSE_HOTKEY,
    SETTINGS_SCHEMA_VERSION,
)
from voxtype.core.models import (
    InjectionMode,
    Language,
    ModelProfile,
    PreprocessingProfile,
    TargetMode,
)


@dataclass(slots=True)
class GeneralSettings:
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    show_overlay: bool = True
    play_sounds: bool = False
    theme: str = "system"
    ui_language: str = "ru"
    onboarding_completed: bool = False


@dataclass(slots=True)
class DictationSettings:
    language: Language = Language.RU
    model_profile: ModelProfile = ModelProfile.BALANCED
    voice_commands: bool = False
    auto_insert: bool = True
    injection_mode: InjectionMode = InjectionMode.CLIPBOARD_PASTE
    target_mode: TargetMode = TargetMode.LOCKED
    insertion_delay_ms: int = 80
    add_fragment_space: bool = True
    capitalize_sentences: bool = True


@dataclass(slots=True)
class AudioSettings:
    device_id: int | None = None
    vad_sensitivity: float = 0.5
    preprocessing_profile: PreprocessingProfile = PreprocessingProfile.NORMAL
    noise_suppression: bool = False
    normalization: bool = True


@dataclass(slots=True)
class ModelSettings:
    installed_model: str = "large-v3-turbo"
    cpu_threads: int = 0
    experimental_gpu: bool = False
    power_mode: str = "balanced"


@dataclass(slots=True)
class PrivacySettings:
    save_history: bool = True
    save_audio: bool = False
    retention_days: int = 30


@dataclass(slots=True)
class HotkeySettings:
    toggle: str = DEFAULT_HOTKEY
    language: str = DEFAULT_LANGUAGE_HOTKEY
    pause: str = DEFAULT_PAUSE_HOTKEY
    push_to_talk: str = "Ctrl+Alt+Shift+Space"


@dataclass(slots=True)
class AppSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    general: GeneralSettings = field(default_factory=GeneralSettings)
    dictation: DictationSettings = field(default_factory=DictationSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    overlay_x: int | None = None
    overlay_y: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
