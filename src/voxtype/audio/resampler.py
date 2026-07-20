"""Offline and streaming mono PCM resampling."""

from __future__ import annotations

from math import gcd
from typing import cast

import numpy as np
import numpy.typing as npt

from .models import FloatAudio, mono_float32


def resample_audio(
    samples: npt.ArrayLike, source_rate: int, target_rate: int = 16_000
) -> FloatAudio:
    """Resample a complete buffer with a polyphase filter when SciPy is present.

    The lightweight interpolation fallback keeps the runtime importable in a
    minimal installation.  Live capture uses ``StreamingResampler`` below so
    boundaries remain continuous across callbacks.
    """

    _validate_rates(source_rate, target_rate)
    audio = mono_float32(samples)
    if source_rate == target_rate or audio.size == 0:
        return audio.copy()
    if audio.size == 1:
        output_size = max(1, int(np.ceil(target_rate / source_rate)))
        return np.full(output_size, audio[0], dtype=np.float32)
    divisor = gcd(source_rate, target_rate)
    up, down = target_rate // divisor, source_rate // divisor
    try:
        from scipy.signal import resample_poly
    except ImportError:
        output_size = max(1, int(np.ceil(audio.size * target_rate / source_rate)))
        positions = np.arange(output_size, dtype=np.float64) * source_rate / target_rate
        result = np.interp(
            positions, np.arange(audio.size, dtype=np.float64), audio, right=float(audio[-1])
        )
    else:
        result = resample_poly(audio, up, down, window=("kaiser", 8.0), padtype="line")
    return np.ascontiguousarray(np.clip(result, -1.0, 1.0), dtype=np.float32)


class StreamingResampler:
    """Stateful low-pass plus interpolation resampler for callback-sized blocks.

    The FIR stage prevents high-frequency energy from aliasing when decimating.
    Absolute fractional source positions are retained, which avoids duplicated
    or missing samples at arbitrary callback boundaries.
    """

    def __init__(
        self, source_rate: int, target_rate: int = 16_000, *, filter_taps: int = 63
    ) -> None:
        _validate_rates(source_rate, target_rate)
        if filter_taps < 7 or filter_taps % 2 == 0:
            raise ValueError("filter_taps must be an odd integer of at least 7")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._step = source_rate / target_rate
        self._total_input = 0
        self._next_position = 0.0
        self._previous: float | None = None
        self._filter = self._design_filter(filter_taps) if target_rate < source_rate else None
        self._filter_state: FloatAudio | None = (
            np.zeros(filter_taps - 1, dtype=np.float32) if self._filter is not None else None
        )

    def _design_filter(self, taps: int) -> FloatAudio:
        cutoff = 0.47 * self.target_rate / self.source_rate
        offsets = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
        kernel = 2.0 * cutoff * np.sinc(2.0 * cutoff * offsets)
        kernel *= np.kaiser(taps, 8.0)
        kernel /= np.sum(kernel)
        return kernel.astype(np.float32)

    def _low_pass(self, audio: FloatAudio) -> FloatAudio:
        if self._filter is None or self._filter_state is None:
            return audio
        combined = np.concatenate((self._filter_state, audio))
        convolved = np.convolve(combined, self._filter, mode="full")
        state_length = self._filter_state.size
        result = convolved[state_length : state_length + audio.size]
        self._filter_state = combined[-state_length:].copy()
        return np.ascontiguousarray(result, dtype=np.float32)

    def process(self, samples: npt.ArrayLike) -> FloatAudio:
        audio = mono_float32(samples)
        if audio.size == 0:
            return np.empty(0, dtype=np.float32)
        if self.source_rate == self.target_rate:
            self._total_input += int(audio.size)
            self._previous = float(audio[-1])
            return audio.copy()

        filtered = self._low_pass(audio)
        start = self._total_input
        end = start + int(filtered.size) - 1
        if self._previous is None:
            x = np.arange(start, end + 1, dtype=np.float64)
            values = filtered
        else:
            x = np.arange(start - 1, end + 1, dtype=np.float64)
            values = cast(
                FloatAudio,
                np.concatenate((np.array([self._previous], dtype=np.float32), filtered)),
            )
        result: FloatAudio
        if self._next_position > end + 1e-12:
            result = np.empty(0, dtype=np.float32)
        else:
            count = int(np.floor((end - self._next_position) / self._step + 1e-12)) + 1
            positions = self._next_position + np.arange(count, dtype=np.float64) * self._step
            result = np.interp(positions, x, values).astype(np.float32)
            self._next_position += count * self._step
        self._total_input += int(audio.size)
        self._previous = float(filtered[-1])
        return np.ascontiguousarray(result, dtype=np.float32)

    def flush(self) -> FloatAudio:
        """Emit fractional tail samples without inventing extra duration."""

        if self._previous is None or self.source_rate == self.target_rate:
            return np.empty(0, dtype=np.float32)
        remaining = self._total_input - self._next_position
        if remaining <= 1e-12:
            return np.empty(0, dtype=np.float32)
        count = int(np.ceil(remaining / self._step - 1e-12))
        self._next_position += count * self._step
        return np.full(count, self._previous, dtype=np.float32)

    def reset(self) -> None:
        self._total_input = 0
        self._next_position = 0.0
        self._previous = None
        if self._filter_state is not None:
            self._filter_state.fill(0.0)


def _validate_rates(source_rate: int, target_rate: int) -> None:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
