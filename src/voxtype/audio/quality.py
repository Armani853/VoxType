"""Passive microphone quality metrics, evaluated on audio/VAD workers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import log10
from time import monotonic

import numpy as np
import numpy.typing as npt

from voxtype.audio.models import AudioInputObservation


class NoiseCategory(StrEnum):
    QUIET = "quiet"
    MODERATE = "moderate"
    NOISY = "noisy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AudioQualitySnapshot:
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    estimated_noise_floor_dbfs: float | None
    low_signal: bool
    clipping_detected: bool
    silence_detected: bool
    sample_rate: int
    updated_at: float

    @property
    def noise_category(self) -> NoiseCategory:
        floor = self.estimated_noise_floor_dbfs
        if floor is None:
            return NoiseCategory.UNKNOWN
        if floor < -55:
            return NoiseCategory.QUIET
        if floor < -40:
            return NoiseCategory.MODERATE
        return NoiseCategory.NOISY


def _dbfs(level: float) -> float:
    return max(-120.0, 20 * log10(max(1e-12, level)))


class AudioQualityMonitor:
    def __init__(self) -> None:
        self._floor: float | None = None
        self._noise_blocks = 0
        self._clipped_blocks = 0
        self._low_blocks = 0
        self._silent_blocks = 0
        self._last = float("-inf")

    @staticmethod
    def measure(pcm: npt.NDArray[np.float32], sample_rate: int) -> AudioInputObservation:
        # Keep all input channels here. Downmix may otherwise hide clipping on one channel.
        if not pcm.size:
            return AudioInputObservation(0.0, 0.0, 0.0, sample_rate)
        nonfinite = pcm.size - int(np.count_nonzero(np.isfinite(pcm)))
        if nonfinite:
            pcm = np.nan_to_num(pcm, nan=0.0, posinf=0.0, neginf=0.0)
        absolute = np.abs(pcm)
        return AudioInputObservation(
            min(1.0, float(np.sqrt(np.mean(np.square(pcm, dtype=np.float64))))),
            min(1.0, float(np.max(absolute))),
            float(np.count_nonzero(absolute >= 0.98) / pcm.size),
            sample_rate,
            int(pcm.shape[0]),
            nonfinite,
        )

    def observe(
        self, observation: AudioInputObservation, *, speech: bool | None
    ) -> AudioQualitySnapshot | None:
        rms = _dbfs(observation.rms)
        self._clipped_blocks = (
            min(3, self._clipped_blocks + 1) if observation.clipping_ratio >= 0.001 else 0
        )
        if speech is True:
            self._low_blocks = min(4, self._low_blocks + 1) if rms < -42 else 0
            self._silent_blocks = 0
        elif speech is False:
            self._low_blocks = 0
            self._silent_blocks = min(8, self._silent_blocks + 1)
            self._noise_blocks = min(12, self._noise_blocks + 1)
            # Smooth the dB estimate and bound the effect of an isolated spike.
            if self._floor is not None:
                self._floor += 0.08 * min(8.0, max(-8.0, rms - self._floor))
            else:
                self._floor = rms

        now = monotonic()
        if now - self._last < 0.2:
            return None
        self._last = now
        return AudioQualitySnapshot(
            rms,
            _dbfs(observation.peak),
            observation.clipping_ratio,
            self._floor if self._noise_blocks >= 12 else None,
            self._low_blocks >= 4,
            self._clipped_blocks >= 3,
            self._silent_blocks >= 8,
            observation.sample_rate,
            now,
        )
