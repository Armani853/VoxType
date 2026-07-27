from pathlib import Path

from tools.run_accuracy_matrix import (
    _aggregate,
    _config_hash,
    _edit_counts,
    _file_id,
    _levenshtein_distance,
    _normalize_words,
    _score,
)


def test_private_score_reports_word_error_components_and_cer() -> None:
    score = _score("Alpha beta gamma.", "alpha delta gamma extra")

    assert score["substitutions"] == 1
    assert score["deletions"] == 0
    assert score["insertions"] == 1
    assert score["reference_words"] == 3
    assert score["pseudo_wer"] == 2 / 3
    assert 0.0 < score["cer"] < 1.0


def test_normalization_folds_case_yo_and_punctuation() -> None:
    assert _normalize_words("Ёлка, API-test!") == ["елка", "api-test"]


def test_edit_alignment_prefers_substitution_to_delete_insert() -> None:
    counts = _edit_counts(["one", "two"], ["one", "three"])

    assert counts.substitutions == 1
    assert counts.deletions == 0
    assert counts.insertions == 0


def test_config_hash_is_stable_for_key_order_and_changes_with_input() -> None:
    left = _config_hash({"options": {"beam": 1, "vad": 500}, "inputs": ["a"]})
    reordered = _config_hash({"inputs": ["a"], "options": {"vad": 500, "beam": 1}})
    changed = _config_hash({"options": {"beam": 3, "vad": 500}, "inputs": ["a"]})

    assert left == reordered
    assert left != changed


def test_file_id_requires_three_digit_prefix() -> None:
    assert _file_id(Path("007_voice.ogg")) == "007"


def test_bit_parallel_character_distance_is_exact() -> None:
    assert _levenshtein_distance("kitten", "sitting") == 3
    assert _levenshtein_distance("", "speech") == 6
    assert _levenshtein_distance("абвгд", "абд") == 2


def test_aggregate_reports_inference_and_end_to_end_rtf() -> None:
    item = {
        "audio_seconds": 10.0,
        "processing_seconds": 4.0,
        "wall_seconds": 6.0,
        "substitutions": 1,
        "deletions": 2,
        "insertions": 3,
        "reference_words": 20,
        "character_errors": 4,
        "reference_characters": 100,
        "vad_segments": 2,
        "retries": 1,
        "cascade_retries": 1,
        "duplicates": 0,
        "hallucinations": 1,
        "uncertain": 1,
    }

    aggregate = _aggregate((item,))

    assert aggregate["rtf"] == 0.4
    assert aggregate["wall_rtf"] == 0.6
    assert aggregate["wall_seconds"] == 6.0
    assert aggregate["cascade_retries"] == 1
    assert aggregate["cascade_retry_percentage"] == 50.0
