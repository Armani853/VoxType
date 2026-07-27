"""Run a wall-clock, lossless VoxType queue/journal soak test.

The default run lasts 60 minutes and samples process resources every 10 seconds.
It uses synthetic PCM and a mock injector, so it is deterministic and never
needs a microphone, an ASR model, or network access.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import numpy.typing as npt
import psutil

from voxtype.asr.base import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from voxtype.asr.hallucination_guard import HallucinationGuard
from voxtype.audio.models import AudioChunk
from voxtype.audio.preprocessing import AudioPreprocessor
from voxtype.audio.spool import AudioQueueLease, DiskSpool, LosslessAudioQueue
from voxtype.core.models import Language, ModelProfile, TranscriptFragment, WordStamp
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.repository import SessionRepository
from voxtype.transcript.merger import TranscriptMerger
from voxtype.vad.base import AdaptiveEnergyVAD
from voxtype.vad.segmenter import AudioSegment, StreamingSegmenter


@dataclass(frozen=True, slots=True)
class SoakConfig:
    duration_seconds: float = 60.0 * 60.0
    sample_interval_seconds: float = 10.0
    chunk_duration_seconds: float = 1.0
    sample_rate: int = 16_000
    max_ram_chunks: int = 4
    spill_threshold: float = 0.5
    backlog_period_chunks: int = 30
    backlog_chunks: int = 6
    normal_processing_factor: float = 0.10
    backlog_processing_factor: float = 1.50
    retry_every_chunks: int = 97
    cascade_every_segments: int = 31
    drain_timeout_seconds: float = 120.0
    max_ram_growth_bytes: int = 128 * 1024 * 1024

    def validate(self) -> None:
        positive = {
            "duration_seconds": self.duration_seconds,
            "sample_interval_seconds": self.sample_interval_seconds,
            "chunk_duration_seconds": self.chunk_duration_seconds,
            "sample_rate": self.sample_rate,
            "max_ram_chunks": self.max_ram_chunks,
            "backlog_period_chunks": self.backlog_period_chunks,
            "drain_timeout_seconds": self.drain_timeout_seconds,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"positive values required: {', '.join(invalid)}")
        if not 0 < self.spill_threshold <= 1:
            raise ValueError("spill_threshold must be in (0, 1]")
        if not 0 <= self.backlog_chunks < self.backlog_period_chunks:
            raise ValueError("backlog_chunks must be below backlog_period_chunks")
        if self.normal_processing_factor < 0 or self.backlog_processing_factor < 0:
            raise ValueError("processing factors cannot be negative")
        if self.retry_every_chunks < 0 or self.cascade_every_segments < 0:
            raise ValueError("retry intervals cannot be negative")


@dataclass(frozen=True, slots=True)
class ResourceSample:
    elapsed_seconds: float
    rss_bytes: int
    cpu_percent: float
    thread_count: int
    queue_chunks: int
    queued_audio_seconds: float
    dropped_frames: int
    spool_bytes: int
    processed_chunks: int


@dataclass(frozen=True, slots=True)
class SoakResult:
    started_at: str
    finished_at: str
    config: dict[str, object]
    samples: list[dict[str, object]]
    summary: dict[str, object]

    @property
    def passed(self) -> bool:
        return bool(self.summary["passed"])

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "config": self.config,
            "samples": self.samples,
            "summary": self.summary,
        }


class _JournalledInjector:
    """Mock injection that verifies durable commit happened before delivery."""

    def __init__(self, repository: SessionRepository, session_id: str) -> None:
        self._repository = repository
        self._session_id = session_id
        self.sequences: list[int] = []
        self.journal_order_failures = 0

    def submit(self, sequence: int, token: str) -> None:
        record = self._repository.get(self._session_id)
        if record is None or not record.committed_text.endswith(token):
            self.journal_order_failures += 1
        self.sequences.append(sequence)


class _DeterministicASR:
    """Backend-shaped local fixture that exercises guard/merger timing paths."""

    def __init__(self, config: SoakConfig, *, retry_model: bool = False) -> None:
        self.config = config
        self.model_id = (
            "deterministic-soak-retry-fixture" if retry_model else "deterministic-soak-fixture"
        )
        self.retry_model = retry_model
        self.segment_sequences: list[int] = []
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        sequence = int(request.metadata.get("segment_sequence", "0"))
        cycle = sequence % self.config.backlog_period_chunks
        factor = (
            self.config.backlog_processing_factor
            if cycle >= self.config.backlog_period_chunks - self.config.backlog_chunks
            else self.config.normal_processing_factor
        )
        started = time.monotonic()
        if factor:
            time.sleep(request.duration_seconds * factor)
        token = f"segment-{sequence}"
        text = token
        if (
            not self.retry_model
            and self.config.cascade_every_segments > 0
            and sequence % self.config.cascade_every_segments == 0
        ):
            text = " ".join(["повтор"] * 9)
        self.segment_sequences.append(sequence)
        word = WordTimestamp(
            text, request.start_offset_seconds, request.start_offset_seconds + 0.2, 0.99
        )
        segment = TranscriptionSegment(
            text,
            request.start_offset_seconds,
            request.start_offset_seconds + 0.2,
            (word,),
            average_log_probability=-0.05,
            no_speech_probability=0.01,
            compression_ratio=1.0,
        )
        return TranscriptionResult(
            text,
            request.language,
            (segment,),
            request.duration_seconds,
            time.monotonic() - started,
            self.model_id,
            "deterministic",
        )


def _audio_samples(sequence: int, frames: int) -> npt.NDArray[np.float32]:
    """Include >18 s monologues, pauses and quiet speech in every cycle."""

    phase = sequence % 48
    amplitude = 0.0 if 25 <= phase < 28 or 38 <= phase < 40 else 0.18
    if 28 <= phase < 38:
        amplitude = 0.04
    if amplitude == 0.0:
        return np.zeros(frames, dtype=np.float32)
    indices = np.arange(frames, dtype=np.float32)
    wave: npt.NDArray[np.float32] = np.asarray(
        amplitude * np.sin(indices * np.float32(0.071)), dtype=np.float32
    )
    return wave


def _resource_sample(
    process: psutil.Process,
    started: float,
    audio_queue: LosslessAudioQueue,
    processed_chunks: int,
) -> ResourceSample:
    stats = audio_queue.snapshot()
    return ResourceSample(
        elapsed_seconds=max(0.0, time.monotonic() - started),
        rss_bytes=process.memory_info().rss,
        cpu_percent=process.cpu_percent(interval=None),
        thread_count=process.num_threads(),
        queue_chunks=stats.ram_chunks + stats.spooled_chunks,
        queued_audio_seconds=stats.queued_audio_seconds,
        dropped_frames=stats.dropped_frames,
        spool_bytes=stats.spooled_bytes,
        processed_chunks=processed_chunks,
    )


def _ram_is_bounded(samples: list[ResourceSample], maximum_growth: int) -> tuple[bool, bool]:
    rss = [sample.rss_bytes for sample in samples]
    if not rss:
        return True, False
    growth_is_small = max(rss) - min(rss) <= maximum_growth
    if len(rss) < 6:
        return growth_is_small, False
    increasing_steps = sum(right >= left for left, right in pairwise(rss))
    linear_growth = (
        increasing_steps / (len(rss) - 1) >= 0.8 and rss[-1] - rss[0] > maximum_growth // 4
    )
    return growth_is_small and not linear_growth, linear_growth


def _threads_are_bounded(
    thread_counts: list[int], base_threads: int, *, worker_alive: bool = False
) -> bool:
    """Allow short native-library bursts while rejecting a retained thread leak."""

    if not thread_counts or worker_alive:
        return False
    peak_allowance = max(4, base_threads // 2)
    return (
        max(thread_counts) <= base_threads + peak_allowance
        and thread_counts[-1] <= base_threads + 1
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def run_soak(config: SoakConfig, work_directory: Path) -> SoakResult:
    """Execute one real wall-clock soak run and return its measured result."""

    config.validate()
    work_directory = work_directory.resolve()
    work_directory.mkdir(parents=True, exist_ok=True)
    spool_directory = work_directory / "spool"
    repository = SessionRepository(work_directory / "sessions.sqlite3")
    session_id = repository.create(
        Language.RU,
        "deterministic-soak-fixture",
        ModelProfile.FAST,
        "mock-injector",
    )
    journal = SessionJournal(repository, session_id)
    journal.append("soak_started", {"duration_seconds": config.duration_seconds})
    injector = _JournalledInjector(repository, session_id)
    spool = DiskSpool(spool_directory, max_bytes=2 * 1024**3)
    audio_queue = LosslessAudioQueue(
        max_ram_chunks=config.max_ram_chunks,
        spill_threshold=config.spill_threshold,
        spool=spool,
        max_inflight=2048,
        durable=True,
    )
    process = psutil.Process()
    process.cpu_percent(interval=None)
    started_utc = datetime.now(UTC).isoformat()
    started = time.monotonic()
    base_threads = process.num_threads()
    frames_per_chunk = max(1, round(config.sample_rate * config.chunk_duration_seconds))
    processed_sequences: list[int] = []
    injected_sequences: list[int] = []
    retried_sequences: set[int] = set()
    exceptions: list[str] = []
    rtf_values: list[float] = []
    latency_values: list[float] = []
    duplicate_count = 0
    retry_count = 0
    cascade_retry_count = 0
    forced_segments = 0
    max_spool_bytes = 0
    preprocessor = AudioPreprocessor(config.sample_rate)
    segmenter = StreamingSegmenter(AdaptiveEnergyVAD(), sample_rate=config.sample_rate)
    merger = TranscriptMerger()
    guard = HallucinationGuard()
    backend = _DeterministicASR(config)
    retry_backend = _DeterministicASR(config, retry_model=True)

    def consume() -> None:
        nonlocal cascade_retry_count, duplicate_count, forced_segments, retry_count
        pending: list[AudioQueueLease] = []
        pending_seconds = 0.0

        def acknowledge_pending() -> None:
            nonlocal duplicate_count, pending_seconds
            while pending:
                completed = pending.pop(0)
                audio_queue.ack(completed)
                if completed.chunk.sequence in processed_sequences:
                    duplicate_count += 1
                processed_sequences.append(completed.chunk.sequence)
            pending_seconds = 0.0

        def acknowledge_committed_prefix() -> None:
            nonlocal pending_seconds
            retained_seconds = segmenter.buffered_seconds
            while pending:
                oldest_seconds = pending[0].chunk.duration_seconds
                if pending_seconds - oldest_seconds + 1e-9 < retained_seconds:
                    break
                completed = pending.pop(0)
                audio_queue.ack(completed)
                processed_sequences.append(completed.chunk.sequence)
                pending_seconds -= oldest_seconds

        def transcribe(segment: AudioSegment) -> None:
            nonlocal cascade_retry_count, forced_segments
            if segment.forced:
                forced_segments += 1
            request = TranscriptionRequest(
                segment.samples,
                segment.sample_rate,
                "ru",
                segment.start_seconds,
                condition_on_previous_text=False,
                metadata={"segment_sequence": str(segment.sequence)},
            )
            guarded = guard.transcribe_guarded(
                backend,
                request,
                committed_text=merger.committed_text,
                retry_backend=retry_backend,
            )
            cascade_retry_count += int(
                guarded.retried and guarded.retry_model_id == retry_backend.model_id
            )
            result = guarded.result
            fragment = TranscriptFragment(
                segment.sequence,
                result.text,
                tuple(
                    WordStamp(item.word, item.start, item.end, item.probability or 0.0)
                    for item in result.words
                ),
                segment.start_seconds,
                segment.end_seconds,
                Language.RU,
                inference_s=result.processing_seconds,
            )
            outcome = merger.merge(fragment)
            if outcome.insertion_text:
                token = f"segment-{segment.sequence} "
                journal.commit_text(token)
                injector.submit(segment.sequence, token)
                injected_sequences.append(segment.sequence)
            rtf_values.append(result.processing_seconds / max(request.duration_seconds, 1e-9))
            latency_values.append(result.processing_seconds)

        try:
            while True:
                lease = audio_queue.acquire(timeout=0.2)
                if lease is None:
                    if audio_queue.closed:
                        break
                    continue
                sequence = lease.chunk.sequence
                should_retry = (
                    config.retry_every_chunks > 0
                    and sequence > 0
                    and sequence % config.retry_every_chunks == 0
                    and sequence not in retried_sequences
                )
                if should_retry:
                    retried_sequences.add(sequence)
                    retry_count += 1
                    audio_queue.nack(lease)
                    continue
                pending.append(lease)
                pending_seconds += lease.chunk.duration_seconds
                processed = preprocessor.process(lease.chunk.samples)
                emitted = segmenter.process(processed.samples)
                for speech_segment in emitted:
                    transcribe(speech_segment)
                acknowledge_committed_prefix()
            for speech_segment in segmenter.flush():
                transcribe(speech_segment)
            acknowledge_pending()
        except Exception as exc:  # the result must retain worker failures
            exceptions.append(type(exc).__name__)
            while pending:
                lease = pending.pop()
                with suppress(ValueError, RuntimeError):
                    audio_queue.nack(lease)

    worker = threading.Thread(target=consume, name="voxtype-soak-consumer", daemon=True)
    worker.start()
    samples: list[ResourceSample] = []
    next_sample = started
    next_chunk = started
    sequence = 0
    try:
        while time.monotonic() - started < config.duration_seconds:
            now = time.monotonic()
            if now >= next_sample:
                samples.append(
                    _resource_sample(process, started, audio_queue, len(processed_sequences))
                )
                next_sample += config.sample_interval_seconds
            if now < next_chunk:
                time.sleep(min(next_chunk - now, 0.05))
                continue
            chunk = AudioChunk(
                sequence,
                _audio_samples(sequence, frames_per_chunk),
                config.sample_rate,
                time.time(),
            )
            audio_queue.put(chunk)
            audio_queue.record_capture(chunk.frames)
            max_spool_bytes = max(max_spool_bytes, audio_queue.snapshot().spooled_bytes)
            sequence += 1
            next_chunk += config.chunk_duration_seconds
    except Exception as exc:
        exceptions.append(type(exc).__name__)
    finally:
        audio_queue.close()

    drained = audio_queue.wait_until_empty(config.drain_timeout_seconds)
    worker.join(timeout=min(config.drain_timeout_seconds, 5.0))
    samples.append(_resource_sample(process, started, audio_queue, len(processed_sequences)))
    stats = audio_queue.snapshot()
    max_spool_bytes = max(max_spool_bytes, *(sample.spool_bytes for sample in samples))
    expected_sequences = list(range(sequence))
    fifo_preserved = processed_sequences == expected_sequences
    audio_not_lost = (
        len(processed_sequences) == sequence
        and stats.captured_frames == sequence * frames_per_chunk
        and stats.dropped_frames == 0
    )
    repository.update_metrics(
        session_id,
        audio_duration_s=stats.processed_audio_seconds,
        processing_s=sum(rtf_values) * config.chunk_duration_seconds,
        dropped_frames=stats.dropped_frames,
    )
    repository.finish(session_id, status="completed" if drained else "soak_failed")
    record = repository.get(session_id)
    journal_tokens = [] if record is None else record.committed_text.split()
    expected_tokens = [f"segment-{sequence}" for sequence in injected_sequences]
    journal_valid = (
        record is not None
        and record.status == "completed"
        and journal_tokens == expected_tokens
        and injector.journal_order_failures == 0
    )
    repository.close()
    spool.close()
    spool_files_remaining = len(tuple(spool_directory.glob("*.vtxpcm")))
    if spool_files_remaining == 0 and spool_directory.exists():
        with suppress(OSError):
            spool_directory.rmdir()
    spool_cleaned = spool_files_remaining == 0 and not spool_directory.exists()
    ram_bounded, linear_ram_growth = _ram_is_bounded(samples, config.max_ram_growth_bytes)
    thread_counts = [sample.thread_count for sample in samples]
    max_threads = max(thread_counts)
    threads_bounded = _threads_are_bounded(
        thread_counts, base_threads, worker_alive=worker.is_alive()
    )
    criteria = {
        "no_exceptions": not exceptions,
        "zero_dropped_frames": stats.dropped_frames == 0,
        "audio_not_lost": audio_not_lost,
        "fifo_preserved": fifo_preserved,
        "queue_drained": drained and math.isclose(stats.queued_audio_seconds, 0.0, abs_tol=1e-6),
        "no_duplicates": duplicate_count == 0,
        "journal_valid": journal_valid,
        "journal_before_injection": injector.journal_order_failures == 0,
        "spool_cleaned": spool_cleaned,
        "ram_bounded": ram_bounded,
        "threads_bounded": threads_bounded,
    }
    elapsed = time.monotonic() - started
    summary: dict[str, object] = {
        "passed": all(criteria.values()),
        "criteria": criteria,
        "wall_clock_seconds": elapsed,
        "captured_chunks": sequence,
        "processed_chunks": len(processed_sequences),
        "captured_frames": stats.captured_frames,
        "dropped_frames": stats.dropped_frames,
        "queue_high_watermark": stats.queue_high_watermark,
        "max_spool_bytes": max_spool_bytes,
        "spool_files_remaining": spool_files_remaining,
        "duplicate_count": duplicate_count,
        "retry_count": retry_count,
        "cascade_retry_count": cascade_retry_count,
        "transcription_segments": len(injected_sequences),
        "forced_segments": forced_segments,
        "exception_types": exceptions,
        "rtf_average": statistics.fmean(rtf_values) if rtf_values else 0.0,
        "rtf_maximum": max(rtf_values, default=0.0),
        "latency_average_seconds": statistics.fmean(latency_values) if latency_values else 0.0,
        "latency_p50_seconds": _percentile(latency_values, 0.50),
        "latency_p95_seconds": _percentile(latency_values, 0.95),
        "latency_maximum_seconds": max(latency_values, default=0.0),
        "rss_min_bytes": min(sample.rss_bytes for sample in samples),
        "rss_max_bytes": max(sample.rss_bytes for sample in samples),
        "linear_ram_growth_detected": linear_ram_growth,
        "base_thread_count": base_threads,
        "max_thread_count": max_threads,
        "final_thread_count": thread_counts[-1],
        "resource_sample_count": len(samples),
    }
    return SoakResult(
        started_at=started_utc,
        finished_at=datetime.now(UTC).isoformat(),
        config={key: value for key, value in asdict(config).items()},
        samples=[{key: value for key, value in asdict(sample).items()} for sample in samples],
        summary=summary,
    )


def write_result(result: SoakResult, output_path: Path) -> None:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)


def reassess_saved_result(output_path: Path) -> SoakResult:
    """Re-evaluate acceptance criteria from an already measured sample series."""

    raw = json.loads(output_path.read_text(encoding="utf-8"))
    samples = raw.get("samples", [])
    summary = raw.get("summary", {})
    if not isinstance(samples, list) or not isinstance(summary, dict):
        raise ValueError("invalid soak result structure")
    counts = [int(item["thread_count"]) for item in samples if isinstance(item, dict)]
    base_threads = int(summary.get("base_thread_count", counts[0] if counts else 0))
    criteria = summary.get("criteria")
    if not isinstance(criteria, dict):
        raise ValueError("soak result has no criteria")
    criteria["threads_bounded"] = _threads_are_bounded(counts, base_threads)
    summary["max_thread_count"] = max(counts, default=0)
    summary["final_thread_count"] = counts[-1] if counts else 0
    summary["passed"] = all(bool(value) for value in criteria.values())
    result = SoakResult(
        started_at=str(raw["started_at"]),
        finished_at=str(raw["finished_at"]),
        config=dict(raw["config"]),
        samples=samples,
        summary=summary,
    )
    write_result(result, output_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=3600.0, help="wall-clock seconds")
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--chunk-duration", type=float, default=1.0)
    parser.add_argument("--work-dir", type=Path, default=Path("soak-results/current"))
    parser.add_argument("--output", type=Path, default=Path("soak-results/soak-result.json"))
    arguments = parser.parse_args()
    config = SoakConfig(
        duration_seconds=arguments.duration,
        sample_interval_seconds=arguments.sample_interval,
        chunk_duration_seconds=arguments.chunk_duration,
    )
    result = run_soak(config, arguments.work_dir)
    write_result(result, arguments.output)
    summary = result.summary
    print(
        "soak "
        f"{'PASS' if result.passed else 'FAIL'}: "
        f"chunks={summary['processed_chunks']}, dropped={summary['dropped_frames']}, "
        f"max_spool={summary['max_spool_bytes']} bytes, output={arguments.output.resolve()}"
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
