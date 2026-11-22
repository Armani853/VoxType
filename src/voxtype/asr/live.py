"""Bounded, provisional speech state for one existing dictation session."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from unicodedata import normalize

import numpy as np

from voxtype.asr.base import LanguageCode, TranscriptionRequest, TranscriptionResult
from voxtype.audio.models import FloatAudio
from voxtype.transcript.formatter import join_word_texts


class SpeechKind(StrEnum):
    PARTIAL = "partial"
    STABLE = "stable"
    FINAL = "final"


class StreamingSpeed(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ECO = "eco"


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    segment_id: str
    session_id: str
    start_sample: int
    end_sample: int
    text: str
    kind: SpeechKind
    revision: int
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class LiveTranscript:
    session_id: str
    stable_segments: tuple[SpeechSegment, ...]
    unstable_text: str
    language: LanguageCode
    updated_at_monotonic: float
    revision: int
    partial_segment: SpeechSegment | None = None


@dataclass(frozen=True, slots=True)
class StreamingConfig:
    first_decode_audio_ms: int = 500
    active_decode_interval_ms: int = 450
    idle_decode_interval_ms: int = 1200
    min_new_audio_ms: int = 250
    max_decode_window_ms: int = 8000
    overlap_ms: int = 1000
    preview_beam_size: int = 1
    max_unconfirmed_ms: int = 12000

    @classmethod
    def for_speed(cls, speed: StreamingSpeed) -> StreamingConfig:
        if speed is StreamingSpeed.FAST:
            return cls(active_decode_interval_ms=350, min_new_audio_ms=200)
        if speed is StreamingSpeed.ECO:
            return cls(active_decode_interval_ms=950, min_new_audio_ms=550)
        return cls()


class LiveSpeechSession:
    def __init__(
        self,
        session_id: str,
        language: LanguageCode,
        model_id: str,
        sample_rate: int,
        *,
        config: StreamingConfig | None = None,
    ) -> None:
        self.session_id = session_id
        self.language = language
        self.model_id = model_id
        self.sample_rate = sample_rate
        config = config or StreamingConfig()
        self.config = config
        self._limit = max(1, round(config.max_decode_window_ms * sample_rate / 1000))
        self._chunks: deque[FloatAudio] = deque()
        self._size = 0
        self._speech_mark = 0
        self._segment_speech_start = 0
        self._next = 0.0
        self._interval = config.active_decode_interval_ms / 1000
        self._decode_time: float | None = None
        self._issued_revision = 0
        self._previous_words: tuple[tuple[str, float, float], ...] = ()
        self._stable: deque[SpeechSegment] = deque(maxlen=24)
        self.processed_offset = 0
        self.revision = 0
        self.canceled = False
        self.snapshot: LiveTranscript | None = None
        self.hotwords: tuple[str, ...] = ()
        self.first_preview = False
        self.first_text = False
        self.first_stable = False

    def offer(self, audio: FloatAudio) -> None:
        if not audio.size:
            return
        self.processed_offset += audio.size
        if audio.size >= self._limit:
            self._chunks.clear()
            audio = audio[-self._limit :]
            self._size = 0
        self._chunks.append(audio)
        self._size += audio.size
        self._discard_before(self.processed_offset - self._limit)

    def _discard_before(self, sample: int) -> None:
        remove = min(self._size, max(0, sample - (self.processed_offset - self._size)))
        while remove:
            first = self._chunks.popleft()
            trim = min(first.size, remove)
            remove -= trim
            self._size -= trim
            if trim < first.size:
                self._chunks.appendleft(first[trim:])

    def request(
        self, speech_samples: int, now: float, *, quiet: bool = False
    ) -> TranscriptionRequest | None:
        minimum_ms = (
            self.config.first_decode_audio_ms
            if self._speech_mark == self._segment_speech_start
            else self.config.min_new_audio_ms
        )

        if (
            self.canceled
            or now < self._next
            or not self._chunks
            or speech_samples - self._speech_mark < minimum_ms * self.sample_rate / 1000
        ):
            return None
        self._speech_mark = speech_samples
        idle_interval = self.config.idle_decode_interval_ms / 1000 if quiet else 0
        self._next = now + max(self._interval, idle_interval)
        self._issued_revision += 1
        return TranscriptionRequest(
            np.concatenate(tuple(self._chunks)),
            self.sample_rate,
            self.language,
            start_offset_seconds=(self.processed_offset - self._size) / self.sample_rate,
            beam_size=self.config.preview_beam_size,
            word_timestamps=self.first_text,
            hotwords=self.hotwords,
            metadata={
                "kind": "preview",
                "session_id": self.session_id,
                "revision": str(self._issued_revision),
            },
        )

    def limit_to_active_audio(self, start_sample: int | None, preroll_samples: int) -> None:
        if start_sample is None:
            start_sample = self.processed_offset - preroll_samples
        self._discard_before(start_sample)

    def publish(
        self, request: TranscriptionRequest, result: TranscriptionResult, elapsed: float
    ) -> None:
        if (
            self.canceled
            or request.metadata.get("session_id") != self.session_id
            or request.metadata.get("revision") != str(self._issued_revision)
            or self._issued_revision <= self.revision
        ):
            return
        now = monotonic()
        self._decode_time = (
            elapsed if self._decode_time is None else 0.75 * self._decode_time + 0.25 * elapsed
        )
        minimum_interval = self.config.active_decode_interval_ms / 1000
        if self._decode_time > self._interval * 0.8:
            self._interval = min(2.5, max(minimum_interval, self._decode_time * 1.5))
        elif self._decode_time < self._interval * 0.45:
            self._interval = max(minimum_interval, self._interval - 0.10)
        self._next = max(self._next, now - elapsed + self._interval)
        start = round(request.start_offset_seconds * self.sample_rate)
        end = start + request.audio.size
        words = result.words
        frontier = self._stable[-1].end_sample if self._stable else start
        floor = max(start, frontier - round(0.05 * self.sample_rate)) / self.sample_rate
        candidates = tuple(
            w for w in words if w.start >= floor and w.end > frontier / self.sample_rate
        )
        previous = tuple(w for w in self._previous_words if w[1] >= floor)
        stable_count = 0
        if candidates and previous:
            safe_end = (
                end - round(self.config.overlap_ms * self.sample_rate / 1000)
            ) / self.sample_rate
            for word, (old_text, old_start, old_end) in zip(candidates, previous, strict=False):
                if (
                    not self._token(word.word)
                    or self._token(word.word) != old_text
                    or abs(word.start - old_start) > 0.15
                    or abs(word.end - old_end) > 0.15
                    or word.end > safe_end
                ):
                    break
                stable_count += 1
        self._previous_words = tuple((self._token(w.word), w.start, w.end) for w in words)
        self.revision = self._issued_revision
        last_stable = frontier
        matched = candidates[:stable_count]
        if matched:
            stable_text = join_word_texts(tuple(word.word for word in matched))
            last_stable = round(matched[-1].end * self.sample_rate)
            self._stable.append(
                SpeechSegment(
                    f"{self.session_id}:stable:{self.revision}",
                    self.session_id,
                    round(matched[0].start * self.sample_rate),
                    last_stable,
                    stable_text,
                    SpeechKind.STABLE,
                    self.revision,
                )
            )
        if self._stable:
            tail = join_word_texts(
                tuple(
                    word.word
                    for word in words
                    if word.start
                    >= (last_stable - round(0.05 * self.sample_rate)) / self.sample_rate
                    and round(word.end * self.sample_rate) > last_stable
                )
            )
        else:
            tail = result.text
        partial = (
            SpeechSegment(
                f"{self.session_id}:partial:{self.revision}",
                self.session_id,
                max(0, start, last_stable),
                end,
                tail,
                SpeechKind.PARTIAL,
                self.revision,
            )
            if tail
            else None
        )
        self.snapshot = LiveTranscript(
            self.session_id,
            tuple(self._stable),
            tail,
            self.language,
            now,
            self.revision,
            partial,
        )

    def finish_segment(self, speech_samples: int, next_start: int) -> None:
        self._discard_before(next_start)
        self._segment_speech_start = speech_samples
        self._speech_mark = speech_samples
        self._next = 0.0
        self.revision = self._issued_revision
        self._previous_words = ()
        self._stable.clear()
        self.snapshot = None

    @staticmethod
    def _token(text: str) -> str:
        return "".join(char for char in normalize("NFKC", text).casefold() if char.isalnum())
