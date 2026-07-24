"""Local Whisper model/decoding profiles and backlog degradation policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class ASRProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    MAXIMUM_ACCURACY = "maximum_accuracy"


@dataclass(frozen=True, slots=True)
class ASRProfileConfig:
    profile: ASRProfile
    model_name: str
    compute_type: str
    beam_size: int
    best_of: int
    cpu_threads: int = 0
    no_speech_threshold: float = 0.55
    log_probability_threshold: float = -1.0
    compression_ratio_threshold: float = 2.4
    word_timestamps: bool = True
    condition_on_previous_text: bool = False

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name cannot be empty")
        if self.beam_size <= 0 or self.best_of <= 0 or self.cpu_threads < 0:
            raise ValueError("invalid decoding/runtime limits")

    def for_backlog(self) -> ASRProfileConfig:
        """Reduce optional search cost while preserving the same local model."""

        return replace(self, beam_size=1, best_of=1, condition_on_previous_text=False)


DEFAULT_PROFILES: dict[ASRProfile, ASRProfileConfig] = {
    ASRProfile.FAST: ASRProfileConfig(
        profile=ASRProfile.FAST,
        model_name="small",
        compute_type="int8",
        beam_size=1,
        best_of=1,
    ),
    ASRProfile.BALANCED: ASRProfileConfig(
        profile=ASRProfile.BALANCED,
        model_name="large-v3-turbo",
        compute_type="int8",
        beam_size=5,
        best_of=5,
    ),
    ASRProfile.MAXIMUM_ACCURACY: ASRProfileConfig(
        profile=ASRProfile.MAXIMUM_ACCURACY,
        model_name="large-v3",
        compute_type="int8_float32",
        beam_size=8,
        best_of=5,
    ),
}


def get_profile(profile: ASRProfile | str) -> ASRProfileConfig:
    key = profile if isinstance(profile, ASRProfile) else ASRProfile(profile)
    return DEFAULT_PROFILES[key]
