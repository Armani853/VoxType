"""Bounded performance telemetry retained only in memory unless exported."""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Literal

import psutil

type TimedOperation = Literal[
    "startup_paint",
    "model_load",
    "ui_stall",
    "delivery",
    "shutdown",
    "audio_to_first_preview",
    "audio_to_first_nonempty_text",
    "audio_to_stable_text",
    "final_segment_latency",
    "stop_to_final",
    "preview_inference",
    "preview_queue_wait",
]


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
        self._processed_chunks = 0
        self._retry_chunks = 0
        self._cascade_retry_chunks = 0
        self._uncertain_chunks = 0
        self._current_model = ""
        self._durations: dict[TimedOperation, tuple[int, float, float]] = {}
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

    def record_operation(self, operation: TimedOperation, seconds: float) -> None:
        with self._lock:
            count, total, longest = self._durations.get(operation, (0, 0.0, 0.0))
            elapsed = max(0.0, seconds)
            self._durations[operation] = (count + 1, total + elapsed, max(longest, elapsed))

    def sync_audio(
        self,
        *,
        captured_frames: int,
        dropped_frames: int,
        queued_audio_seconds: float,
        processed_audio_seconds: float,
        queue_high_watermark: int,
        callback_overflows: int,
        current_input_rms: float,
        clipping_percentage: float,
    ) -> None:
        """Publish authoritative capture/queue counters into diagnostics."""

        with self._lock:
            self.audio.captured_frames = max(0, int(captured_frames))
            self.audio.dropped_frames = max(0, int(dropped_frames))
            self.audio.queued_audio_seconds = max(0.0, float(queued_audio_seconds))
            self.audio.processed_audio_seconds = max(0.0, float(processed_audio_seconds))
            self.audio.queue_high_watermark = max(0, int(queue_high_watermark))
            self.audio.callback_overflows = max(0, int(callback_overflows))
            self.audio.current_input_rms = max(0.0, float(current_input_rms))
            self.audio.clipping_percentage = max(0.0, float(clipping_percentage))

    def record_processed(
        self,
        audio_seconds: float,
        inference_seconds: float,
        latency_s: float,
        *,
        model_id: str = "",
        retried: bool = False,
        cascade_retry: bool = False,
        uncertain: bool = False,
    ) -> None:
        with self._lock:
            # The durable queue publishes processed_audio_seconds through
            # sync_audio().  ASR segments overlap, so adding their duration
            # here would double-count audio while still useful for RTF.
            if audio_seconds > 0:
                self._rtfs.append(inference_seconds / audio_seconds)
            self._latencies.append(max(0.0, latency_s))
            self._processed_chunks += 1
            self._retry_chunks += int(retried)
            self._cascade_retry_chunks += int(cascade_retry)
            self._uncertain_chunks += int(uncertain)
            if model_id:
                self._current_model = model_id

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
            processed = self._processed_chunks
            return {
                "audio": asdict(self.audio),
                "current_rtf": rtfs[-1] if rtfs else 0.0,
                "rtf_average": statistics.fmean(rtfs) if rtfs else 0.0,
                "rtf_max": max(rtfs, default=0.0),
                "latency_average_s": statistics.fmean(latencies) if latencies else 0.0,
                "latency_max_s": max(latencies, default=0.0),
                "latency_p50_s": _percentile(latencies, 0.50),
                "latency_p95_s": _percentile(latencies, 0.95),
                "peak_rss_bytes": max(
                    (sample.rss_bytes for sample in samples),
                    default=0,
                ),
                "current_model": self._current_model,
                "processed_chunks": processed,
                "retry_chunks": self._retry_chunks,
                "cascade_retry_chunks": self._cascade_retry_chunks,
                "cascade_retry_percentage": (
                    self._cascade_retry_chunks * 100.0 / processed if processed else 0.0
                ),
                "uncertain_chunks": self._uncertain_chunks,
                "operation_timings_ms": {
                    name: {"count": count, "average": 1000 * total / count, "max": 1000 * longest}
                    for name, (count, total, longest) in self._durations.items()
                },
                "samples": [asdict(sample) for sample in samples],
            }


def _percentile(values: tuple[float, ...], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
