from __future__ import annotations

from pathlib import Path

import pytest

from voxtype.core.models import Language, TranscriptFragment, WordStamp
from voxtype.transcript.commands import VoiceCommandProcessor
from voxtype.transcript.formatter import format_fragment_boundary
from voxtype.transcript.merger import (
    TranscriptMerger,
    deduplicate_text_boundary,
    normalize_token,
)
from voxtype.transcript.vocabulary import Vocabulary


def fragment(
    sequence: int,
    text: str,
    words: list[str],
    chunk_start: float,
    chunk_end: float,
) -> TranscriptFragment:
    stamps = tuple(
        WordStamp(word, index * 0.4, (index + 1) * 0.4, 0.95) for index, word in enumerate(words)
    )
    return TranscriptFragment(
        sequence=sequence,
        text=text,
        words=stamps,
        chunk_start_s=chunk_start,
        chunk_end_s=chunk_end,
        language=Language.EN,
    )


def test_merger_removes_exact_timed_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "Hello from VoxType", ["Hello", "from", "VoxType"], 0.0, 1.2))
    result = merger.merge(fragment(1, "from VoxType today", ["from", "VoxType", "today"], 0.4, 1.6))
    assert result.skipped_prefix_words == 2
    assert result.insertion_text == " today"
    assert result.committed_text == "Hello from VoxType today"


def test_recovery_boundary_removes_only_replayed_suffix_prefix() -> None:
    assert (
        deduplicate_text_boundary(
            "Это уже сохранённый важный текст", "важный текст и восстановленный хвост"
        )
        == "и восстановленный хвост"
    )
    assert deduplicate_text_boundary("один два", "совсем другой текст") == "совсем другой текст"


def test_merger_ignores_punctuation_and_case_at_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "Привет, мир", ["Привет,", "мир"], 0.0, 0.8))
    result = merger.merge(fragment(1, "МИР! дальше", ["МИР!", "дальше"], 0.35, 1.15))
    assert result.skipped_prefix_words == 1
    assert result.committed_text == "Привет, мир дальше"


def test_merger_handles_one_word_disagreement_in_long_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "one two three four", ["one", "two", "three", "four"], 0.0, 1.6))
    result = merger.merge(
        fragment(1, "one too three four five", ["one", "too", "three", "four", "five"], 0.2, 2.2)
    )
    assert result.skipped_prefix_words == 4
    assert result.committed_text.endswith(" five")


def test_merger_preserves_real_repeat_without_audio_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "yes yes", ["yes", "yes"], 0.0, 0.8))
    result = merger.merge(fragment(1, "yes yes", ["yes", "yes"], 1.0, 1.8))
    assert result.skipped_prefix_words == 0
    assert result.committed_text == "Yes yes yes yes"


def test_merger_uses_text_fallback_when_word_timestamps_are_missing() -> None:
    merger = TranscriptMerger()
    first = TranscriptFragment(0, "one two three four", (), 0.0, 2.0, Language.EN)
    second = TranscriptFragment(1, "three four five", (), 1.0, 3.0, Language.EN)
    merger.merge(first)
    result = merger.merge(second)
    assert result.skipped_prefix_words == 2
    assert result.committed_text == "One two three four five"


def test_merger_uses_text_fallback_when_only_previous_timestamps_are_missing() -> None:
    merger = TranscriptMerger()
    merger.merge(TranscriptFragment(0, "one two three four", (), 0.0, 2.0, Language.EN))
    result = merger.merge(fragment(1, "three four five", ["three", "four", "five"], 1.0, 3.0))
    assert result.skipped_prefix_words == 2
    assert result.committed_text == "One two three four five"


def test_merger_text_fallback_preserves_repeat_after_non_overlapping_pause() -> None:
    merger = TranscriptMerger()
    first = TranscriptFragment(0, "repeat this", (), 0.0, 1.0, Language.EN)
    second = TranscriptFragment(1, "repeat this", (), 1.2, 2.2, Language.EN)
    merger.merge(first)
    result = merger.merge(second)
    assert result.skipped_prefix_words == 0
    assert result.committed_text == "Repeat this repeat this"


def test_merger_rejects_replayed_sequence() -> None:
    merger = TranscriptMerger()
    first = merger.merge(fragment(5, "first", ["first"], 0.0, 0.4))
    duplicate = merger.merge(fragment(5, "different", ["different"], 1.0, 1.4))
    assert not first.duplicate_detected
    assert duplicate.duplicate_detected
    assert duplicate.insertion_text == ""
    assert duplicate.committed_text == "First"
    assert [word.text for word in merger.committed_words] == ["first"]


def test_merger_keeps_real_adjacent_short_repeat_and_removes_only_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "go go", ["go", "go"], 0.0, 0.8))
    result = merger.merge(fragment(1, "go now", ["go", "now"], 0.4, 1.2))
    assert result.skipped_prefix_words == 1
    assert result.committed_text == "Go go now"


def test_merger_handles_cyrillic_latin_numbers_hyphen_and_abbreviation() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "VoxType тест API-2", ["VoxType", "тест", "API-2"], 0.0, 1.2))
    result = merger.merge(fragment(1, "тест API-2 готов", ["тест", "API-2", "готов"], 0.4, 1.6))
    assert result.skipped_prefix_words == 2
    assert result.committed_text == "VoxType тест API-2 готов"


def test_merger_accepts_empty_chunk_without_losing_next_text() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "first", ["first"], 0.0, 0.4))
    empty = merger.merge(TranscriptFragment(1, "", (), 0.4, 0.8, Language.EN))
    result = merger.merge(fragment(2, "second", ["second"], 0.8, 1.2))
    assert empty.insertion_text == ""
    assert result.committed_text == "First second"


def test_merger_removes_ten_word_forced_chunk_overlap() -> None:
    merger = TranscriptMerger(max_overlap_words=20)
    previous = [f"w{index}" for index in range(15)]
    incoming = [*previous[-10:], "tail"]
    merger.merge(fragment(0, " ".join(previous), previous, 0.0, 6.0))
    result = merger.merge(fragment(1, " ".join(incoming), incoming, 2.0, 6.4))
    assert result.skipped_prefix_words == 10
    assert result.committed_text.endswith(" tail")


def test_merger_long_dictation_remains_ordered() -> None:
    merger = TranscriptMerger()
    for sequence in range(500):
        start = sequence * 0.5
        merger.merge(fragment(sequence, f"word{sequence}", [f"word{sequence}"], start, start + 0.4))
    tokens = merger.committed_text.split()
    assert len(tokens) == 500
    assert tokens[0] == "Word0"
    assert tokens[-1] == "word499"


def test_formatter_spacing() -> None:
    assert format_fragment_boundary("Hello", ", world") == ", world"
    assert format_fragment_boundary("Hello ", "world") == "world"
    assert format_fragment_boundary("Done. ", "next") == "Next"
    assert format_fragment_boundary("Line", "\nNext") == "\nNext"


def test_text_boundary_recovery_validates_limit_and_handles_full_duplicate() -> None:
    with pytest.raises(ValueError):
        deduplicate_text_boundary("one", "one", max_words=0)
    assert deduplicate_text_boundary("one two", "one two") == ""
    assert deduplicate_text_boundary("one two", "different") == "different"


def test_merger_validates_overlap_limit_and_normalizes_symbol_only_token() -> None:
    with pytest.raises(ValueError):
        TranscriptMerger(max_overlap_words=0)
    assert normalize_token("...") == ""


def test_voice_commands_are_disabled_by_default() -> None:
    processor = VoiceCommandProcessor()
    assert processor.process("слово точка", Language.RU) == "слово точка"


def test_voice_commands_only_replace_whitelist() -> None:
    processor = VoiceCommandProcessor(enabled=True)
    assert processor.process("Привет запятая мир точка", Language.RU) == "Привет, мир."
    assert processor.process("hello new line world", Language.EN) == "hello\nworld"


def test_vocabulary_is_bounded_deduplicated_and_atomic(tmp_path: Path) -> None:
    vocabulary = Vocabulary(max_entries=2)
    assert vocabulary.add("PySide6")
    assert not vocabulary.add("pyside6")
    assert vocabulary.add("CTranslate2")
    assert not vocabulary.add("Whisper")
    path = tmp_path / "vocabulary.json"
    vocabulary.save(path)
    loaded = Vocabulary.load(path)
    assert loaded.entries == ("PySide6", "CTranslate2")
    assert len(loaded.initial_prompt(30)) <= 30
