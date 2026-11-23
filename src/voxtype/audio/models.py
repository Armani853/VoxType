"""Typed audio data shared by capture, buffering, VAD and ASR."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

import numpy as np
import numpy.typing as npt

FloatAudio = npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate_hz: int
    channels: int
    sample_format: str
    device_id: str


class CaptureDownmixer:
    """Average coherent stereo and retain one channel when averaging cancels speech."""

    def __init__(self) -> None:
        self._energy: npt.NDArray[np.float64] | None = None
        self._combined_energy = 0.0
        self._selected: int | None = None

    def process(self, samples: FloatAudio) -> FloatAudio:
        finite = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
        if finite.ndim == 1 or finite.shape[1] == 1:
            return finite.reshape(-1)
        if not finite.size:
            return np.empty(0, dtype=np.float32)
        mixed = finite.mean(axis=1, dtype=np.float32)
        energy = np.square(finite, dtype=np.float64).mean(axis=0)
        combined = float(np.square(mixed, dtype=np.float64).mean())
        if self._energy is None:
            self._energy = energy
            self._combined_energy = combined
        else:
            self._energy = 0.8 * self._energy + 0.2 * energy
            self._combined_energy = 0.8 * self._combined_energy + 0.2 * combined
        strongest = int(np.argmax(self._energy))
        if self._selected is None and self._combined_energy < 0.16 * self._energy[strongest]:
            self._selected = strongest
        if self._selected is not None:
            return finite[:, self._selected]
        return np.asarray(mixed, dtype=np.float32)


def mono_float32(samples: npt.ArrayLike, *, copy: bool = False) -> FloatAudio:
    """Return contiguous, finite mono PCM in the canonical ``[-1, 1]`` range.

    Integer PCM is scaled using the full positive range of its dtype.  Float
    input is clipped because out-of-range values make level and VAD decisions
    misleading and are not valid normalized PCM.
    """

    array = np.asarray(samples)
    if np.issubdtype(array.dtype, np.integer):
        limits = np.iinfo(array.dtype)
        scale = float(max(abs(limits.min), limits.max))
        result = array.astype(np.float32) / scale
    else:
        result = array.astype(np.float32, copy=False)
    if result.ndim == 2:
        result = result[:, 0] if result.shape[1] == 1 else np.mean(result, axis=1)
    if result.ndim != 1:
        raise ValueError("audio must be a one-dimensional mono array")
    if not np.all(np.isfinite(result)):
        result = np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=-1.0)
    if copy:
        canonical = np.empty(result.shape, dtype=np.float32)
        np.clip(result, -1.0, 1.0, out=canonical)
        return canonical
    return np.ascontiguousarray(np.clip(result, -1.0, 1.0), dtype=np.float32)


@dataclass(frozen=True, slots=True)
class AudioInputObservation:
    rms: float
    peak: float
    clipping_ratio: float
    sample_rate: int
    frames: int = 0
    nonfinite_samples: int = 0


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """A monotonically sequenced block captured from one input stream."""

    sequence: int
    samples: FloatAudio = field(repr=False)
    sample_rate: int = 16_000
    captured_at: float = field(default_factory=time)
    input_overflow: bool = False
    session_id: int | None = None
    sample_offset: int | None = None
    input_quality: AudioInputObservation | None = None

    @property
    def channels(self) -> int:
        return 1

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.sample_offset is not None and self.sample_offset < 0:
            raise ValueError("sample_offset must be non-negative")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        canonical = mono_float32(self.samples, copy=True)
        canonical.setflags(write=False)
        object.__setattr__(self, "samples", canonical)

    @property
    def frames(self) -> int:
        return int(self.samples.size)

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate


@dataclass(frozen=True, slots=True)
class AudioQueueStats:
    """A consistent point-in-time view of capture/backlog metrics."""

    captured_frames: int = 0
    dropped_frames: int = 0
    queued_audio_seconds: float = 0.0
    processed_audio_seconds: float = 0.0
    queue_high_watermark: int = 0
    callback_overflows: int = 0
    current_input_rms: float = 0.0
    clipping_percentage: float = 0.0
    ram_chunks: int = 0
    spooled_chunks: int = 0
    spooled_bytes: int = 0
    spilling_to_disk: bool = False
