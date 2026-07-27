"""Backend-neutral ASR contracts and immutable transcription models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, runtime_checkable

import numpy as np

from voxtype.audio.models import FloatAudio, mono_float32

LanguageCode = Literal["ru", "en"]


class ASRError(RuntimeError):
    """Base exception for a local recognizer failure."""


class ASRDependencyUnavailable(ASRError):
    """The selected optional inference runtime is not installed."""


class ASRModelUnavailable(ASRError):
    """A requested model is absent, invalid, or cannot be loaded."""


@dataclass(frozen=True, slots=True)
class WordTimestamp:
    word: str
    start: float
    end: float
    probability: float | None = None

    def __post_init__(self) -> None:
        if self.start < 0.0 or self.end < self.start:
            raise ValueError("invalid word timestamps")
        if self.probability is not None and not 0.0 <= self.probability <= 1.0:
            raise ValueError("word probability must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    text: str
    start: float
    end: float
    words: tuple[WordTimestamp, ...] = ()
    average_log_probability: float | None = None
    no_speech_probability: float | None = None
    compression_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.start < 0.0 or self.end < self.start:
            raise ValueError("invalid segment timestamps")


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio: FloatAudio = field(repr=False)
    sample_rate: int = 16_000
    language: LanguageCode = "ru"
    start_offset_seconds: float = 0.0
    initial_prompt: str = ""
    beam_size: int | None = None
    temperature: float = 0.0
    condition_on_previous_text: bool = False
    no_speech_threshold: float | None = None
    log_probability_threshold: float | None = None
    compression_ratio_threshold: float | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        audio = mono_float32(self.audio, copy=True)
        audio.setflags(write=False)
        object.__setattr__(self, "audio", audio)
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.language not in ("ru", "en"):
            raise ValueError("language must be explicitly 'ru' or 'en'")
        if self.start_offset_seconds < 0.0:
            raise ValueError("start_offset_seconds must be non-negative")
        if self.beam_size is not None and self.beam_size <= 0:
            raise ValueError("beam_size must be positive")
        if self.temperature < 0.0:
            raise ValueError("temperature must be non-negative")

    @property
    def duration_seconds(self) -> float:
        return self.audio.size / self.sample_rate

    def conservative_retry(self) -> TranscriptionRequest:
        """Remove propagating context and use one deterministic wider search."""

        return replace(
            self,
            initial_prompt="",
            beam_size=max(5, self.beam_size or 1),
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.55,
            log_probability_threshold=-0.8,
            compression_ratio_threshold=2.2,
        )


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: LanguageCode
    segments: tuple[TranscriptionSegment, ...]
    audio_duration_seconds: float
    processing_seconds: float
    model_id: str
    backend_name: str
    retry_count: int = 0
    possible_inaccuracy: bool = False
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.audio_duration_seconds < 0.0 or self.processing_seconds < 0.0:
            raise ValueError("durations must be non-negative")

    @property
    def words(self) -> tuple[WordTimestamp, ...]:
        return tuple(word for segment in self.segments for word in segment.words)

    @property
    def real_time_factor(self) -> float:
        if self.audio_duration_seconds <= 0.0:
            return 0.0
        return self.processing_seconds / self.audio_duration_seconds

    @property
    def average_word_probability(self) -> float | None:
        probabilities = [word.probability for word in self.words if word.probability is not None]
        if not probabilities:
            return None
        return float(np.mean(probabilities))

    def marked_uncertain(
        self, warnings: tuple[str, ...], *, retry_count: int | None = None
    ) -> TranscriptionResult:
        return replace(
            self,
            possible_inaccuracy=True,
            warnings=tuple(dict.fromkeys((*self.warnings, *warnings))),
            retry_count=self.retry_count if retry_count is None else retry_count,
        )


@runtime_checkable
class ASRBackend(Protocol):
    @property
    def model_id(self) -> str:
        """Stable model identifier used in journals and diagnostics."""

    @property
    def loaded(self) -> bool:
        """Whether model weights currently occupy runtime resources."""

    def load(self) -> None:
        """Load local model resources, raising a typed error on failure."""

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Perform one bounded, explicit-language transcription."""

    def unload(self) -> None:
        """Release model resources deterministically where the runtime permits."""


def bounded_prompt(
    confirmed_context: str,
    vocabulary: tuple[str, ...] = (),
    *,
    max_characters: int = 600,
    max_context_words: int = 30,
) -> str:
    """Build a small soft hint without feeding an entire transcript back in."""

    if max_characters <= 0 or max_context_words < 0:
        return ""
    cleaned_terms = [" ".join(term.split()) for term in vocabulary if term.strip()]
    vocabulary_hint = ", ".join(dict.fromkeys(cleaned_terms))
    if len(vocabulary_hint) > max_characters // 2:
        vocabulary_hint = _fit_words(vocabulary_hint, max_characters // 2, keep_tail=False)
    context_words = confirmed_context.split()
    context = " ".join(context_words[-max_context_words:]) if max_context_words else ""
    separator_size = 2 if context and vocabulary_hint else 0
    context = _fit_words(
        context,
        max(0, max_characters - len(vocabulary_hint) - separator_size),
        keep_tail=True,
    )
    return ". ".join(part for part in (context, vocabulary_hint) if part).rstrip(" ,.")


def _fit_words(text: str, limit: int, *, keep_tail: bool) -> str:
    """Fit complete whitespace-delimited tokens into a character budget."""

    words = text.split()
    while words and len(" ".join(words)) > limit:
        if keep_tail:
            words.pop(0)
        else:
            words.pop()
    return " ".join(words).rstrip(" ,.")
