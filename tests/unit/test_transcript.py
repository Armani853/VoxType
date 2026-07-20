from __future__ import annotations

from pathlib import Path

from voxtype.core.models import Language, TranscriptFragment, WordStamp
from voxtype.transcript.commands import VoiceCommandProcessor
from voxtype.transcript.formatter import format_fragment_boundary
from voxtype.transcript.merger import TranscriptMerger
from voxtype.transcript.vocabulary import Vocabulary


def fragment(
    sequence: int,
    text: str,
    words: list[str],
    chunk_start: float,
    chunk_end: float,
) -> TranscriptFragment:
    stamps = tuple(
        WordStamp(word, index * 0.4, (index + 1) * 0.4, 0.95)
        for index, word in enumerate(words)
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
    result = merger.merge(
        fragment(1, "from VoxType today", ["from", "VoxType", "today"], 0.4, 1.6)
    )
    assert result.skipped_prefix_words == 2
    assert result.insertion_text == " today"
    assert result.committed_text == "Hello from VoxType today"


def test_merger_ignores_punctuation_and_case_at_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(0, "Привет, мир", ["Привет,", "мир"], 0.0, 0.8))
    result = merger.merge(fragment(1, "МИР! дальше", ["МИР!", "дальше"], 0.35, 1.15))
    assert result.skipped_prefix_words == 1
    assert result.committed_text == "Привет, мир дальше"


def test_merger_handles_one_word_disagreement_in_long_overlap() -> None:
    merger = TranscriptMerger()
    merger.merge(
        fragment(0, "one two three four", ["one", "two", "three", "four"], 0.0, 1.6)
    )
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


def test_merger_rejects_replayed_sequence() -> None:
    merger = TranscriptMerger()
    merger.merge(fragment(5, "first", ["first"], 0.0, 0.4))
    duplicate = merger.merge(fragment(5, "first", ["first"], 0.0, 0.4))
    assert duplicate.duplicate_detected
    assert duplicate.insertion_text == ""


def test_formatter_spacing() -> None:
    assert format_fragment_boundary("Hello", ", world") == ", world"
    assert format_fragment_boundary("Hello ", "world") == "world"
    assert format_fragment_boundary("Done. ", "next") == "Next"
    assert format_fragment_boundary("Line", "\nNext") == "\nNext"


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
