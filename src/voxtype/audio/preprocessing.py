"""Conservative, stateful speech preprocessing profiles."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from .levels import AudioLevels, LevelAnalyzer
from .models import FloatAudio, mono_float32


class PreprocessingProfile(StrEnum):
    RAW = "raw"
    NORMAL = "normal"
    NOISY_ROOM = "noisy_room"


@dataclass(frozen=True, slots=True)
class PreprocessorSettings:
    profile: PreprocessingProfile = PreprocessingProfile.NORMAL
    remove_dc: bool = True
    high_pass_hz: float = 70.0
    normalization_enabled: bool = True
    target_rms_dbfs: float = -22.0
    maximum_gain_db: float = 15.0
    limiter_threshold: float = 0.92
    noise_suppression_enabled: bool = False
    noise_floor_gain: float = 0.35

    def __post_init__(self) -> None:
        if not 0.0 <= self.high_pass_hz < 1_000.0:
            raise ValueError("high_pass_hz is outside the speech-safe range")
        if not -40.0 <= self.target_rms_dbfs <= -10.0:
            raise ValueError("target_rms_dbfs must be between -40 and -10 dBFS")
        if not 0.0 <= self.maximum_gain_db <= 30.0:
            raise ValueError("maximum_gain_db must be between 0 and 30 dB")
        if not 0.5 <= self.limiter_threshold < 1.0:
            raise ValueError("limiter_threshold must be in [0.5, 1)")
        if not 0.0 < self.noise_floor_gain <= 1.0:
            raise ValueError("noise_floor_gain must be in (0, 1]")

    @classmethod
    def for_profile(cls, profile: PreprocessingProfile) -> PreprocessorSettings:
        if profile is PreprocessingProfile.RAW:
            return cls(
                profile=profile, remove_dc=False, high_pass_hz=0.0, normalization_enabled=False
            )
        if profile is PreprocessingProfile.NOISY_ROOM:
            return cls(
                profile=profile,
                high_pass_hz=90.0,
                target_rms_dbfs=-24.0,
                maximum_gain_db=10.0,
                noise_suppression_enabled=True,
                noise_floor_gain=0.25,
            )
        return cls(profile=profile)


@dataclass(frozen=True, slots=True)
class PreprocessedAudio:
    samples: FloatAudio
    input_levels: AudioLevels
    output_levels: AudioLevels
    applied_gain_db: float


class AudioPreprocessor:
    """Improve audibility while refusing to amplify learned room tone."""

    def __init__(
        self, sample_rate: int = 16_000, settings: PreprocessorSettings | None = None
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self.sample_rate = sample_rate
        self.settings = settings or PreprocessorSettings()
        self.input_analyzer = LevelAnalyzer()
        self.output_analyzer = LevelAnalyzer()
        self._previous_x = 0.0
        self._previous_y = 0.0
        self._smoothed_gain = 1.0

    def process(self, samples: npt.ArrayLike) -> PreprocessedAudio:
        audio = mono_float32(samples, copy=True)
        input_levels = self.input_analyzer.analyze(audio)
        if audio.size == 0 or self.settings.profile is PreprocessingProfile.RAW:
            output_levels = self.output_analyzer.analyze(audio)
            return PreprocessedAudio(audio, input_levels, output_levels, 0.0)

        processed = (
            self._high_pass(audio)
            if self.settings.remove_dc or self.settings.high_pass_hz
            else audio
        )
        if self.settings.noise_suppression_enabled:
            processed = self._soft_noise_gate(processed, input_levels.rms)
        gain = self._normalization_gain(input_levels.rms)
        processed = processed * np.float32(gain)
        processed = self._soft_limiter(processed)
        processed = np.ascontiguousarray(np.clip(processed, -1.0, 1.0), dtype=np.float32)
        output_levels = self.output_analyzer.analyze(processed)
        return PreprocessedAudio(
            processed, input_levels, output_levels, 20.0 * math.log10(max(gain, 1e-12))
        )

    def _high_pass(self, audio: FloatAudio) -> FloatAudio:
        cutoff = max(10.0, self.settings.high_pass_hz)
        coefficient = math.exp(-2.0 * math.pi * cutoff / self.sample_rate)
        result = np.empty_like(audio)
        previous_x = self._previous_x
        previous_y = self._previous_y
        for index, value in enumerate(audio):
            current = float(value)
            previous_y = coefficient * (previous_y + current - previous_x)
            previous_x = current
            result[index] = previous_y
        self._previous_x = previous_x
        self._previous_y = previous_y
        return result

    def _normalization_gain(self, rms: float) -> float:
        if not self.settings.normalization_enabled:
            self._smoothed_gain = 1.0
            return 1.0
        noise = self.input_analyzer.noise_floor_rms
        # Absolute and relative gates stop stationary silence from being lifted
        # to speech level even during the initial floor-learning period.
        is_speech_like = rms >= 10.0 ** (-52.0 / 20.0) and rms >= noise * 2.5
        if is_speech_like:
            target = 10.0 ** (self.settings.target_rms_dbfs / 20.0)
            desired = min(target / max(rms, 1e-8), 10.0 ** (self.settings.maximum_gain_db / 20.0))
            desired = max(0.5, desired)
            smoothing = 0.20 if desired < self._smoothed_gain else 0.06
        else:
            desired = 1.0
            smoothing = 0.15
        self._smoothed_gain += smoothing * (desired - self._smoothed_gain)
        return self._smoothed_gain

    def _soft_noise_gate(self, audio: FloatAudio, rms: float) -> FloatAudio:
        noise = max(self.input_analyzer.noise_floor_rms, 1e-8)
        ratio = rms / noise
        if ratio >= 3.5:
            gain = 1.0
        else:
            mix = min(1.0, max(0.0, (ratio - 1.0) / 2.5))
            gain = (
                self.settings.noise_floor_gain + (1.0 - self.settings.noise_floor_gain) * mix * mix
            )
        return audio * np.float32(gain)

    def _soft_limiter(self, audio: FloatAudio) -> FloatAudio:
        threshold = self.settings.limiter_threshold
        magnitude = np.abs(audio)
        above = magnitude > threshold
        if not np.any(above):
            return audio
        result = audio.copy()
        headroom = 1.0 - threshold
        limited = threshold + headroom * np.tanh((magnitude[above] - threshold) / headroom)
        result[above] = np.sign(audio[above]) * limited
        return result

    def reset(self) -> None:
        self._previous_x = 0.0
        self._previous_y = 0.0
        self._smoothed_gain = 1.0
        self.input_analyzer = LevelAnalyzer()
        self.output_analyzer = LevelAnalyzer()
