"""Microphone level, clipping and slowly varying noise-floor analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from .models import mono_float32


class InputLevelStatus(StrEnum):
    NO_SIGNAL = "no_signal"
    TOO_QUIET = "too_quiet"
    NORMAL = "normal"
    CLIPPING = "clipping"


@dataclass(frozen=True, slots=True)
class AudioLevels:
    rms: float
    peak: float
    dbfs: float
    clipping_percentage: float
    noise_floor_dbfs: float
    status: InputLevelStatus


class LevelAnalyzer:
    """Analyze blocks while learning the floor only from quiet observations."""

    def __init__(
        self,
        *,
        clipping_threshold: float = 0.985,
        quiet_dbfs: float = -42.0,
        no_signal_dbfs: float = -75.0,
        clipping_warning_percent: float = 0.1,
        noise_smoothing: float = 0.04,
    ) -> None:
        if not 0.0 < clipping_threshold <= 1.0:
            raise ValueError("clipping_threshold must be in (0, 1]")
        if not 0.0 < noise_smoothing <= 1.0:
            raise ValueError("noise_smoothing must be in (0, 1]")
        self.clipping_threshold = clipping_threshold
        self.quiet_dbfs = quiet_dbfs
        self.no_signal_dbfs = no_signal_dbfs
        self.clipping_warning_percent = clipping_warning_percent
        self.noise_smoothing = noise_smoothing
        self._noise_rms: float = float(10.0 ** (-60.0 / 20.0))

    @property
    def noise_floor_rms(self) -> float:
        return self._noise_rms

    def analyze(self, samples: npt.ArrayLike) -> AudioLevels:
        audio = mono_float32(samples)
        if audio.size == 0:
            return AudioLevels(
                0.0, 0.0, -120.0, 0.0, self._to_db(self._noise_rms), InputLevelStatus.NO_SIGNAL
            )
        absolute = np.abs(audio)
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        peak = float(np.max(absolute))
        dbfs = self._to_db(rms)
        clipping = float(np.count_nonzero(absolute >= self.clipping_threshold) * 100.0 / audio.size)

        # A block far above the learned floor is probably speech and must not
        # raise the floor, otherwise quiet words following a loud word vanish.
        if rms <= max(self._noise_rms * 2.5, 10.0 ** (self.quiet_dbfs / 20.0)):
            alpha = self.noise_smoothing
            self._noise_rms = (1.0 - alpha) * self._noise_rms + alpha * max(rms, 1e-8)

        if clipping >= self.clipping_warning_percent:
            status = InputLevelStatus.CLIPPING
        elif dbfs <= self.no_signal_dbfs:
            status = InputLevelStatus.NO_SIGNAL
        elif dbfs < self.quiet_dbfs:
            status = InputLevelStatus.TOO_QUIET
        else:
            status = InputLevelStatus.NORMAL
        return AudioLevels(rms, peak, dbfs, clipping, self._to_db(self._noise_rms), status)

    @staticmethod
    def _to_db(value: float) -> float:
        return max(-120.0, 20.0 * math.log10(max(value, 1e-12)))
