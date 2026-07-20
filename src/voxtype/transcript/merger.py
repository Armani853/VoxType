"""Time-aware overlap merger for independently transcribed audio chunks.

The merger removes a suffix/prefix match only when the corresponding chunks
actually overlap in time. This protects deliberate repetitions spoken after a
pause. Exact text equality alone is deliberately insufficient.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from voxtype.core.models import TranscriptFragment, WordStamp
from voxtype.transcript.formatter import format_fragment_boundary, join_word_texts

_TOKEN_PATTERN = re.compile(r"[\w]+(?:[-'’][\w]+)*", re.UNICODE)


def normalize_token(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    match = _TOKEN_PATTERN.search(normalized)
    return match.group(0) if match else ""


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    insertion_text: str
    committed_text: str
    skipped_prefix_words: int
    duplicate_detected: bool


class TranscriptMerger:
    def __init__(self, max_overlap_words: int = 20, timestamp_tolerance_s: float = 0.35):
        if max_overlap_words < 1:
            raise ValueError("max_overlap_words must be positive")
        self.max_overlap_words = max_overlap_words
        self.timestamp_tolerance_s = timestamp_tolerance_s
        self._words: list[WordStamp] = []
        self._text = ""
        self._last_sequence = -1
        self._last_chunk_end_s = 0.0
        self._lock = threading.RLock()

    @property
    def committed_text(self) -> str:
        with self._lock:
            return self._text

    @property
    def committed_words(self) -> tuple[WordStamp, ...]:
        with self._lock:
            return tuple(self._words)

    def reset(self) -> None:
        with self._lock:
            self._words.clear()
            self._text = ""
            self._last_sequence = -1
            self._last_chunk_end_s = 0.0

    def merge(self, fragment: TranscriptFragment) -> MergeOutcome:
        with self._lock:
            if fragment.sequence <= self._last_sequence:
                return MergeOutcome("", self._text, 0, True)
            absolute = self._absolute_words(fragment)
            overlap_count = self._find_overlap(absolute, fragment.chunk_start_s)
            remaining = absolute[overlap_count:]
            raw_insertion = self._text_after_prefix(fragment.text, overlap_count)
            if remaining and (not raw_insertion or overlap_count >= len(absolute)):
                raw_insertion = join_word_texts([word.text for word in remaining])
            insertion = format_fragment_boundary(self._text, raw_insertion)
            if insertion:
                self._text += insertion
            self._words.extend(remaining)
            self._last_sequence = fragment.sequence
            self._last_chunk_end_s = max(self._last_chunk_end_s, fragment.chunk_end_s)
            return MergeOutcome(insertion, self._text, overlap_count, overlap_count > 0)

    def _absolute_words(self, fragment: TranscriptFragment) -> list[WordStamp]:
        words = list(fragment.words)
        if not words:
            return []
        duration = fragment.audio_duration_s
        timestamps_look_relative = (
            fragment.chunk_start_s > 0
            and min(word.start_s for word in words) >= -0.05
            and max(word.end_s for word in words) <= duration + 0.5
        )
        return [word.shifted(fragment.chunk_start_s) for word in words] if timestamps_look_relative else words

    def _find_overlap(self, new_words: list[WordStamp], chunk_start_s: float) -> int:
        if not self._words or not new_words:
            return 0
        # A repeated phrase in a later, non-overlapping chunk is real speech.
        # Deduplication is permitted only for an actual forced-segment overlap.
        if chunk_start_s >= self._last_chunk_end_s - 0.02:
            return 0
        limit = min(self.max_overlap_words, len(self._words), len(new_words))
        previous_tokens = [normalize_token(word.text) for word in self._words]
        new_tokens = [normalize_token(word.text) for word in new_words]
        for size in range(limit, 0, -1):
            left = previous_tokens[-size:]
            right = new_tokens[:size]
            if not all(left) or not all(right):
                continue
            exact = left == right
            fuzzy = size >= 4 and SequenceMatcher(a=left, b=right, autojunk=False).ratio() >= 0.75
            if not (exact or fuzzy):
                continue
            previous_start = self._words[-size].start_s
            previous_end = self._words[-1].end_s
            new_start = new_words[0].start_s
            new_end = new_words[size - 1].end_s
            timestamps_valid = previous_end > previous_start and new_end > new_start
            temporally_overlapping = (
                not timestamps_valid
                or (
                    new_start <= previous_end + self.timestamp_tolerance_s
                    and new_end >= previous_start - self.timestamp_tolerance_s
                )
            )
            if temporally_overlapping:
                return size
        return 0

    @staticmethod
    def _text_after_prefix(text: str, word_count: int) -> str:
        if word_count <= 0:
            return text.strip()
        matches = list(_TOKEN_PATTERN.finditer(text))
        if word_count >= len(matches):
            return ""
        return text[matches[word_count].start() :].lstrip(" ,.;:!?—–-\t\r\n")
