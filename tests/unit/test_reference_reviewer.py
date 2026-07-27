from pathlib import Path

from tools.review_references import _items, _time_label


def test_reference_reviewer_pairs_by_numeric_prefix_without_modifying_source(
    tmp_path: Path,
) -> None:
    audio_directory = tmp_path / "audio"
    reference_directory = tmp_path / "references"
    audio_directory.mkdir()
    reference_directory.mkdir()
    audio = audio_directory / "001_voice_sample.ogg"
    reference = reference_directory / "001_voice_sample.txt"
    audio.write_bytes(b"fixture")
    reference.write_text("synthetic reference\n", encoding="utf-8")

    pairs = _items(audio_directory, reference_directory, "*.ogg")

    assert len(pairs) == 1
    assert pairs[0].audio == audio.resolve()
    assert pairs[0].supplied_reference == reference.resolve()
    assert reference.read_text(encoding="utf-8") == "synthetic reference\n"


def test_reference_reviewer_formats_seek_time() -> None:
    assert _time_label(0) == "00:00"
    assert _time_label(125_900) == "02:05"
