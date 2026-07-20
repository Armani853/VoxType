"""Backend-neutral local speech recognition for VoxType."""

from .base import (
    ASRBackend,
    ASRDependencyUnavailable,
    ASRError,
    ASRModelUnavailable,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
    bounded_prompt,
)
from .benchmark import BenchmarkCase, BenchmarkResult, benchmark_backend, select_profile
from .faster_whisper_backend import FasterWhisperBackend
from .hallucination_guard import (
    GuardAssessment,
    GuardedTranscription,
    HallucinationGuard,
    HallucinationIssue,
)
from .model_manager import InstalledModel, ModelManager, ModelSpec
from .profiles import ASRProfile, ASRProfileConfig, get_profile

__all__ = [
    "ASRBackend",
    "ASRDependencyUnavailable",
    "ASRError",
    "ASRModelUnavailable",
    "ASRProfile",
    "ASRProfileConfig",
    "BenchmarkCase",
    "BenchmarkResult",
    "FasterWhisperBackend",
    "GuardAssessment",
    "GuardedTranscription",
    "HallucinationGuard",
    "HallucinationIssue",
    "InstalledModel",
    "ModelManager",
    "ModelSpec",
    "TranscriptionRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
    "WordTimestamp",
    "benchmark_backend",
    "bounded_prompt",
    "get_profile",
    "select_profile",
]
