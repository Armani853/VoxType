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


class SpeechKind(StrEnum):
    PARTIAL = "partial"
    STABLE = "stable"
    FINAL = "final"


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


class LiveSpeechSession:
    def __init__(
        self, session_id: str, language: LanguageCode, model_id: str, sample_rate: int,
        *, window_seconds: float = 6.0,
    ) -> None:
        self.session_id = session_id
        self.language = language
        self.model_id = model_id
        self.sample_rate = sample_rate
        self._limit = max(1, int(min(6.0, max(1.0, window_seconds)) * sample_rate))
        self._chunks: deque[FloatAudio] = deque()
        self._size = 0
        self._speech_mark = 0
        self._next = monotonic() + 1.5
        self._interval = 1.5
        self._previous_start = -1
        self._previous_words: tuple[tuple[str, float, float], ...] = ()
        self._stable: deque[SpeechSegment] = deque(maxlen=24)
        self.processed_offset = 0
        self.revision = 0
        self.canceled = False
        self.snapshot: LiveTranscript | None = None

    def offer(self, audio: FloatAudio) -> None:
        self.processed_offset += audio.size
        if audio.size >= self._limit:
            self._chunks.clear()
            audio = audio[-self._limit:]
            self._size = 0
        self._chunks.append(audio)
        self._size += audio.size
        while self._size > self._limit:
            first = self._chunks.popleft()
            trim = min(first.size, self._size - self._limit)
            self._size -= trim
            if trim < first.size:
                self._chunks.appendleft(first[trim:])

    def request(self, speech_samples: int, now: float) -> TranscriptionRequest | None:
        if (self.canceled or now < self._next or not self._chunks
                or speech_samples - self._speech_mark < 0.8 * self.sample_rate):
            return None
        self._speech_mark = speech_samples
        self._next = now + self._interval
        return TranscriptionRequest(
            np.concatenate(tuple(self._chunks)), self.sample_rate, self.language,
            start_offset_seconds=(self.processed_offset - self._size) / self.sample_rate,
            beam_size=1, metadata={"kind": "preview", "session_id": self.session_id},
        )

    def publish(self, request: TranscriptionRequest, result: TranscriptionResult, elapsed: float) -> None:
        if self.canceled:
            return
        now = monotonic()
        self._interval = min(4.0, self._interval + 0.75) if elapsed > 0.8 else max(1.5, self._interval - 0.15)
        self._next = max(self._next, now + self._interval)
        start = round(request.start_offset_seconds * self.sample_rate)
        words = result.words
        stable_count = 0
        if start == self._previous_start:
            safe_end = self.processed_offset / self.sample_rate - 0.6
            for word, (old_text, old_start, old_end) in zip(words, self._previous_words):
                if (
                    not self._token(word.word) or self._token(word.word) != old_text
                    or abs(word.start - old_start) > 0.15 or abs(word.end - old_end) > 0.15
                    or word.end > safe_end
                ):
                    break
                stable_count += 1
        self._previous_start = start
        self._previous_words = tuple((self._token(w.word), w.start, w.end) for w in words)
        self.revision += 1
        last_stable = self._stable[-1].end_sample if self._stable else -1
        matched = tuple(
            word for word in words[:stable_count]
            if round(word.start * self.sample_rate) >= last_stable - round(0.05 * self.sample_rate)
            and round(word.end * self.sample_rate) > last_stable
        )
        if matched:
            stable_text = "".join(word.word for word in matched).strip()
            last_stable = round(matched[-1].end * self.sample_rate)
            self._stable.append(SpeechSegment(
                f"{self.session_id}:stable:{self.revision}", self.session_id,
                round(matched[0].start * self.sample_rate), last_stable,
                stable_text, SpeechKind.STABLE, self.revision,
            ))
        if self._stable:
            tail = "".join(
                word.word for word in words
                if round(word.end * self.sample_rate) > last_stable + round(0.1 * self.sample_rate)
            ).strip()
        else:
            tail = result.text
        partial = SpeechSegment(
            f"{self.session_id}:partial:{self.revision}", self.session_id,
            max(0, start, last_stable), self.processed_offset,
            tail, SpeechKind.PARTIAL, self.revision,
        ) if tail else None
        self.snapshot = LiveTranscript(
            self.session_id, tuple(self._stable), tail, self.language, now, self.revision, partial,
        )

    def finish_segment(self, speech_samples: int) -> None:
        self._chunks.clear()
        self._size = 0
        self._speech_mark = speech_samples
        self._previous_start = -1
        self._previous_words = ()
        self._stable.clear()
        self.snapshot = None

    @staticmethod
    def _token(text: str) -> str:
        return "".join(char for char in normalize("NFKC", text).casefold() if char.isalnum())
