"""Bounded performance telemetry retained only in memory unless exported."""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

import psutil


@dataclass(slots=True)
class AudioCounters:
    captured_frames: int = 0
    dropped_frames: int = 0
    queued_audio_seconds: float = 0.0
    processed_audio_seconds: float = 0.0
    queue_high_watermark: int = 0
    callback_overflows: int = 0
    current_input_rms: float = 0.0
    clipping_percentage: float = 0.0


@dataclass(frozen=True, slots=True)
class ResourceSample:
    monotonic_time: float
    rss_bytes: int
    cpu_percentage: float
    thread_count: int
    queue_size: int
    spool_bytes: int


class PerformanceMonitor:
    def __init__(self, max_samples: int = 720, max_chunks: int = 512):
        self.audio = AudioCounters()
        self._samples: deque[ResourceSample] = deque(maxlen=max_samples)
        self._rtfs: deque[float] = deque(maxlen=max_chunks)
        self._latencies: deque[float] = deque(maxlen=max_chunks)
        self._lock = threading.RLock()
        self._process = psutil.Process()
        self._process.cpu_percent(None)

    def record_capture(
        self,
        frames: int,
        *,
        rms: float,
        clipping_percentage: float,
        queue_size: int,
        queued_seconds: float,
        overflow: bool = False,
    ) -> None:
        with self._lock:
            self.audio.captured_frames += frames
            self.audio.current_input_rms = rms
            self.audio.clipping_percentage = clipping_percentage
            self.audio.queued_audio_seconds = queued_seconds
            self.audio.queue_high_watermark = max(self.audio.queue_high_watermark, queue_size)
            if overflow:
                self.audio.callback_overflows += 1

    def record_drop(self, frames: int) -> None:
        with self._lock:
            self.audio.dropped_frames += frames

    def record_processed(self, audio_seconds: float, inference_seconds: float, latency_s: float) -> None:
        with self._lock:
            self.audio.processed_audio_seconds += audio_seconds
            if audio_seconds > 0:
                self._rtfs.append(inference_seconds / audio_seconds)
            self._latencies.append(max(0.0, latency_s))

    def sample_resources(self, queue_size: int, spool_bytes: int) -> ResourceSample:
        sample = ResourceSample(
            monotonic_time=time.monotonic(),
            rss_bytes=self._process.memory_info().rss,
            cpu_percentage=self._process.cpu_percent(None),
            thread_count=self._process.num_threads(),
            queue_size=queue_size,
            spool_bytes=spool_bytes,
        )
        with self._lock:
            self._samples.append(sample)
        return sample

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rtfs = tuple(self._rtfs)
            latencies = tuple(self._latencies)
            samples = tuple(self._samples)
            return {
                "audio": asdict(self.audio),
                "rtf_average": statistics.fmean(rtfs) if rtfs else 0.0,
                "rtf_max": max(rtfs, default=0.0),
                "latency_average_s": statistics.fmean(latencies) if latencies else 0.0,
                "latency_max_s": max(latencies, default=0.0),
                "samples": [asdict(sample) for sample in samples],
            }

