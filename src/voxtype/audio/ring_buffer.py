"""Fixed-capacity mono audio ring buffer."""

from __future__ import annotations

from collections import deque
from threading import RLock

import numpy as np
import numpy.typing as npt

from .models import FloatAudio, mono_float32


class AudioRingBuffer:
    """Keep only the newest samples without reallocating one giant stream.

    Chunks are retained in a deque and trimmed at its left edge.  This makes the
    common capture operation O(1), while ``tail`` performs the deliberate copy
    needed to hand an immutable snapshot to downstream code.
    """

    def __init__(self, capacity_frames: int) -> None:
        if capacity_frames <= 0:
            raise ValueError("capacity_frames must be positive")
        self._capacity = capacity_frames
        self._chunks: deque[FloatAudio] = deque()
        self._frames = 0
        self._lock = RLock()

    @property
    def capacity_frames(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return self._frames

    def append(self, samples: npt.ArrayLike) -> None:
        audio = mono_float32(samples, copy=True)
        if audio.size == 0:
            return
        if audio.size >= self._capacity:
            audio = audio[-self._capacity :].copy()
            with self._lock:
                self._chunks.clear()
                self._chunks.append(audio)
                self._frames = int(audio.size)
            return

        with self._lock:
            self._chunks.append(audio)
            self._frames += int(audio.size)
            excess = self._frames - self._capacity
            while excess > 0:
                first = self._chunks[0]
                if first.size <= excess:
                    self._chunks.popleft()
                    self._frames -= int(first.size)
                    excess -= int(first.size)
                else:
                    self._chunks[0] = first[excess:].copy()
                    self._frames -= excess
                    excess = 0

    def tail(self, frames: int | None = None) -> FloatAudio:
        if frames is not None and frames < 0:
            raise ValueError("frames must be non-negative")
        with self._lock:
            wanted = self._frames if frames is None else min(frames, self._frames)
            if wanted == 0:
                return np.empty(0, dtype=np.float32)
            result = np.concatenate(tuple(self._chunks))
        return np.ascontiguousarray(result[-wanted:], dtype=np.float32)

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._frames = 0
