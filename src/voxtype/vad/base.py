"""Voice-activity interfaces and a dependency-free adaptive energy fallback."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from voxtype.audio.models import mono_float32


@runtime_checkable
class VoiceActivityDetector(Protocol):
    def speech_probability(self, samples: npt.ArrayLike, sample_rate: int) -> float:
        """Return a calibrated speech likelihood in ``[0, 1]``."""

    def reset(self) -> None:
        """Forget stream-specific recurrent/noise state."""


@dataclass(frozen=True, slots=True)
class EnergyVADSettings:
    minimum_dbfs: float = -58.0
    snr_midpoint_db: float = 7.0
    snr_slope_db: float = 2.5
    initial_noise_dbfs: float = -55.0
    noise_update_rate: float = 0.035

    def __post_init__(self) -> None:
        if self.snr_slope_db <= 0.0:
            raise ValueError("snr_slope_db must be positive")
        if not 0.0 < self.noise_update_rate <= 1.0:
            raise ValueError("noise_update_rate must be in (0, 1]")


class AdaptiveEnergyVAD:
    """Stable local fallback when the optional Silero runtime is unavailable.

    It is intentionally conservative: this detector only gates segmentation;
    it never alters the samples later sent to ASR.
    """

    def __init__(self, settings: EnergyVADSettings | None = None) -> None:
        self.settings = settings or EnergyVADSettings()
        self._initial_noise = 10.0 ** (self.settings.initial_noise_dbfs / 20.0)
        self._noise_rms = self._initial_noise

    @property
    def noise_floor_dbfs(self) -> float:
        return 20.0 * math.log10(max(self._noise_rms, 1e-12))

    def speech_probability(self, samples: npt.ArrayLike, sample_rate: int) -> float:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        audio = mono_float32(samples)
        if audio.size == 0:
            return 0.0
        # A Hann weighting reduces decisions dominated by a discontinuity at a
        # block boundary without changing the audio passed downstream.
        weighted = audio * np.hanning(audio.size).astype(np.float32) if audio.size > 2 else audio
        rms = float(np.sqrt(np.mean(np.square(weighted, dtype=np.float64))))
        dbfs = 20.0 * math.log10(max(rms, 1e-12))
        snr_db = 20.0 * math.log10(max(rms, 1e-12) / max(self._noise_rms, 1e-12))
        snr_probability = 1.0 / (
            1.0 + math.exp(-(snr_db - self.settings.snr_midpoint_db) / self.settings.snr_slope_db)
        )
        absolute_probability = 1.0 / (1.0 + math.exp(-(dbfs - self.settings.minimum_dbfs) / 2.5))
        probability = min(1.0, max(0.0, snr_probability * absolute_probability))

        if probability < 0.35 or rms < self._noise_rms * 1.7:
            alpha = self.settings.noise_update_rate
            # Downward adaptation is faster than upward adaptation so a burst
            # of quiet speech cannot permanently redefine itself as noise.
            if rms > self._noise_rms:
                alpha *= 0.2
            self._noise_rms = (1.0 - alpha) * self._noise_rms + alpha * max(rms, 1e-8)
        return probability

    def reset(self) -> None:
        self._noise_rms = self._initial_noise
