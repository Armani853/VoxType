"""Time-aware overlap merger for independently transcribed audio chunks.

The merger removes a suffix/prefix match only when the corresponding chunks
actually overlap in time. This protects deliberate repetitions spoken after a
pause. Exact text equality alone is deliberately insufficient.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from bisect import bisect_right
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import islice

from voxtype.core.models import TranscriptFragment, WordStamp
from voxtype.transcript.formatter import format_fragment_boundary, join_word_texts

_TOKEN_PATTERN = re.compile(r"[\w]+(?:[-'’][\w]+)*", re.UNICODE)


def normalize_token(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    match = _TOKEN_PATTERN.search(normalized)
    return match.group(0) if match else ""


def deduplicate_text_boundary(previous: str, continuation: str, max_words: int = 20) -> str:
    """Remove a repeated textual suffix/prefix after crash recovery.

    Recovery intentionally replays the durable segmenter tail. Its timestamps
    predate the new process, so text-boundary matching is the only stable way
    to discard the already committed overlap.
    """

    if max_words < 1:
        raise ValueError("max_words must be positive")
    previous_matches = list(_TOKEN_PATTERN.finditer(previous))
    continuation_matches = list(_TOKEN_PATTERN.finditer(continuation))
    limit = min(max_words, len(previous_matches), len(continuation_matches))
    previous_tokens = [normalize_token(item.group(0)) for item in previous_matches]
    continuation_tokens = [normalize_token(item.group(0)) for item in continuation_matches]
    overlap = 0
    for size in range(limit, 0, -1):
        if previous_tokens[-size:] == continuation_tokens[:size]:
            overlap = size
            break
    if overlap == 0:
        return continuation.strip()
    if overlap >= len(continuation_matches):
        return ""
    return continuation[continuation_matches[overlap].start() :].lstrip(" ,.;:!?—–-\t\r\n")


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
        self._text: str | None = ""
        self._pieces: list[str] = []
        self._ends: list[int] = []
        self._tail = ""
        self._last_sequence = -1
        self._last_chunk_end_s = 0.0
        self._lock = threading.RLock()

    @property
    def committed_text(self) -> str:
        with self._lock:
            if self._text is None:
                self._text = "".join(self._pieces)
            return self._text

    @property
    def committed_length(self) -> int:
        with self._lock:
            return self._ends[-1] if self._ends else 0

    @property
    def recent_text(self) -> str:
        with self._lock:
            return self._tail

    def text_since(self, offset: int, limit: int) -> str:
        with self._lock:
            offset = max(offset, 0)
            index = bisect_right(self._ends, offset)
            stop = bisect_right(self._ends, limit, lo=index)
            if index == len(self._ends) or limit <= offset:
                return ""
            base = self._ends[index - 1] if index else 0
            return "".join(self._pieces[index : stop + 1])[offset - base : limit - base]

    @property
    def committed_words(self) -> tuple[WordStamp, ...]:
        with self._lock:
            return tuple(self._words)

    def set_final_text(self, text: str) -> None:
        """Replace the display text after final routing, retaining raw word timings."""
        with self._lock:
            self._text = text
            self._pieces[:] = [text] if text else []
            self._ends[:] = [len(text)] if text else []
            self._tail = text[-4096:]

    def reset(self) -> None:
        with self._lock:
            self._words.clear()
            self._text = ""
            self._pieces.clear()
            self._ends.clear()
            self._tail = ""
            self._last_sequence = -1
            self._last_chunk_end_s = 0.0

    def merge(
        self,
        fragment: TranscriptFragment,
        *,
        add_space: bool = True,
        capitalize: bool = True,
        transform: Callable[[str], str] | None = None,
        preserve_format: bool = False,
        code_spacing: bool = False,
        include_committed_text: bool = True,
    ) -> MergeOutcome:
        with self._lock:
            if fragment.sequence <= self._last_sequence:
                return MergeOutcome(
                    "", self.committed_text if include_committed_text else "", 0, True
                )
            absolute = self._absolute_words(fragment)
            overlap_count = (
                self._find_overlap(
                    absolute, fragment.chunk_start_s, strict=fragment.absolute_word_timestamps
                )
                if absolute
                else 0
                if fragment.absolute_word_timestamps
                else self._find_text_overlap(fragment.text, fragment.chunk_start_s)
            )
            # A previous backend result may have text but no word stamps.  In
            # that mixed case the timestamp matcher has no history to compare,
            # so fall back to the time-gated text boundary matcher.
            if (
                absolute
                and overlap_count == 0
                and not self._words
                and not fragment.absolute_word_timestamps
            ):
                overlap_count = self._find_text_overlap(fragment.text, fragment.chunk_start_s)
            remaining = absolute[overlap_count:]
            raw_insertion = self._text_after_prefix(fragment.text, overlap_count)
            if remaining and (not raw_insertion or overlap_count >= len(absolute)):
                raw_insertion = join_word_texts([word.text for word in remaining])
            if raw_insertion and transform is not None:
                raw_insertion = transform(raw_insertion)
            insertion = (
                raw_insertion
                if raw_insertion in {"\n", "\n\n"}
                else (
                    format_fragment_boundary(
                        self._tail,
                        raw_insertion,
                        add_space=add_space
                        and not (
                            code_spacing
                            and (self._tail.endswith("_") or raw_insertion.startswith(("_", "(")))
                        ),
                        capitalize=capitalize,
                        normalize=not preserve_format,
                    )
                )
            )
            if insertion:
                self._ends.append((self._ends[-1] if self._ends else 0) + len(insertion))
                self._pieces.append(insertion)
                self._tail = (self._tail + insertion)[-4096:]
                self._text = None
            self._words.extend(remaining)
            self._last_sequence = fragment.sequence
            self._last_chunk_end_s = max(self._last_chunk_end_s, fragment.chunk_end_s)
            return MergeOutcome(
                insertion,
                self.committed_text if include_committed_text else "",
                overlap_count,
                overlap_count > 0,
            )

    def _absolute_words(self, fragment: TranscriptFragment) -> list[WordStamp]:
        words = list(fragment.words)
        if not words or fragment.absolute_word_timestamps:
            return words
        duration = fragment.audio_duration_s
        timestamps_look_relative = (
            fragment.chunk_start_s > 0
            and min(word.start_s for word in words) >= -0.05
            and max(word.end_s for word in words) <= duration + 0.5
        )
        return (
            [word.shifted(fragment.chunk_start_s) for word in words]
            if timestamps_look_relative
            else words
        )

    def _find_overlap(
        self, new_words: list[WordStamp], chunk_start_s: float, *, strict: bool = False
    ) -> int:
        if not self._words or not new_words:
            return 0
        # A repeated phrase in a later, non-overlapping chunk is real speech.
        # Deduplication is permitted only for an actual forced-segment overlap.
        if chunk_start_s >= self._last_chunk_end_s - 0.02:
            return 0
        limit = min(self.max_overlap_words, len(self._words), len(new_words))
        previous_tokens = [normalize_token(word.text) for word in self._words[-limit:]]
        new_tokens = [normalize_token(word.text) for word in new_words[:limit]]
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
            temporally_aligned = not timestamps_valid or (
                abs(new_start - previous_start) <= self.timestamp_tolerance_s
                and abs(new_end - previous_end) <= self.timestamp_tolerance_s
            )
            if strict:
                temporally_aligned = (
                    timestamps_valid
                    and all(
                        min(old.end_s, new.end_s, self._last_chunk_end_s)
                        > max(old.start_s, new.start_s, chunk_start_s)
                        for old, new in zip(self._words[-size:], new_words[:size], strict=True)
                    )
                    and temporally_aligned
                )
            if temporally_aligned:
                return size
        return 0

    def _find_text_overlap(self, text: str, chunk_start_s: float) -> int:
        """Fallback for backends that return missing or unusable word times.

        Text-only de-duplication is deliberately allowed only when the audio
        chunks overlap.  A later identical sentence is therefore preserved as
        an intentional repetition.
        """

        if not self._tail or chunk_start_s >= self._last_chunk_end_s - 0.02:
            return 0
        tail = deque(
            (match.group(0) for match in _TOKEN_PATTERN.finditer(self._tail)),
            maxlen=self.max_overlap_words,
        )
        previous = [normalize_token(word) for word in tail]
        incoming = [
            normalize_token(match.group(0))
            for match in islice(_TOKEN_PATTERN.finditer(text), self.max_overlap_words)
        ]
        limit = min(self.max_overlap_words, len(previous), len(incoming))
        for size in range(limit, 0, -1):
            left = previous[-size:]
            right = incoming[:size]
            if left == right or (
                size >= 4 and SequenceMatcher(a=left, b=right, autojunk=False).ratio() >= 0.75
            ):
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
