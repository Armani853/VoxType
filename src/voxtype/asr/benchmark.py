"""Deterministic local ASR benchmark and profile selection by measured RTF."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from time import perf_counter

from .base import ASRBackend, TranscriptionRequest
from .profiles import ASRProfile


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    request: TranscriptionRequest
    name: str = "sample"


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    profile: ASRProfile
    model_id: str
    successful: bool
    mean_rtf: float
    maximum_rtf: float
    mean_latency_seconds: float
    audio_seconds: float
    processing_seconds: float
    chunks: int
    error: str | None = None


def benchmark_backend(
    profile: ASRProfile,
    backend: ASRBackend,
    cases: Iterable[BenchmarkCase],
    *,
    clock: Callable[[], float] = perf_counter,
) -> BenchmarkResult:
    materialized = tuple(cases)
    if not materialized:
        raise ValueError("at least one benchmark case is required")
    latencies: list[float] = []
    rtfs: list[float] = []
    audio_seconds = 0.0
    try:
        backend.load()
        for case in materialized:
            started = clock()
            result = backend.transcribe(case.request)
            wall_time = max(0.0, clock() - started)
            # A mocked backend may report synthetic processing time; wall time
            # remains the comparable end-to-end benchmark measurement.
            latency = wall_time if wall_time > 0.0 else result.processing_seconds
            duration = case.request.duration_seconds
            latencies.append(latency)
            rtfs.append(latency / duration if duration > 0.0 else 0.0)
            audio_seconds += duration
    except Exception as exc:
        return BenchmarkResult(
            profile=profile,
            model_id=backend.model_id,
            successful=False,
            mean_rtf=float("inf"),
            maximum_rtf=float("inf"),
            mean_latency_seconds=float("inf"),
            audio_seconds=audio_seconds,
            processing_seconds=sum(latencies),
            chunks=len(latencies),
            error=f"{type(exc).__name__}: {exc}",
        )
    return BenchmarkResult(
        profile=profile,
        model_id=backend.model_id,
        successful=True,
        mean_rtf=fmean(rtfs),
        maximum_rtf=max(rtfs),
        mean_latency_seconds=fmean(latencies),
        audio_seconds=audio_seconds,
        processing_seconds=sum(latencies),
        chunks=len(latencies),
    )


def select_profile(
    results: Mapping[ASRProfile, BenchmarkResult] | Iterable[BenchmarkResult],
    *,
    target_rtf: float = 0.7,
) -> ASRProfile:
    """Choose the most accurate profile with measured headroom below target."""

    if target_rtf <= 0.0:
        raise ValueError("target_rtf must be positive")
    values = tuple(results.values()) if isinstance(results, Mapping) else tuple(results)
    successful = [item for item in values if item.successful]
    if not successful:
        raise RuntimeError("no ASR profile completed the benchmark")
    accuracy_order = {
        ASRProfile.FAST: 0,
        ASRProfile.BALANCED: 1,
        ASRProfile.MAXIMUM_ACCURACY: 2,
    }
    eligible = [item for item in successful if item.maximum_rtf <= target_rtf]
    if eligible:
        return max(eligible, key=lambda item: accuracy_order[item.profile]).profile
    return min(successful, key=lambda item: (item.maximum_rtf, item.mean_rtf)).profile


def save_benchmark_results(path: str | Path, results: Iterable[BenchmarkResult]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for result in results:
        data = asdict(result)
        data["profile"] = result.profile.value
        payload.append(data)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def load_benchmark_results(path: str | Path) -> tuple[BenchmarkResult, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("benchmark results must be a JSON list")
    return tuple(BenchmarkResult(profile=ASRProfile(item.pop("profile")), **item) for item in raw)
