from tools.evaluate_wer import error_counts, normalize


def test_wer_counts_each_error_kind() -> None:
    counts = error_counts(["one", "two"], ["one", "too"])
    assert counts.substitutions == 1
    assert counts.deletions == 0
    assert counts.insertions == 0
    assert counts.wer == 0.5


def test_wer_handles_deletion_and_insertion() -> None:
    deletion = error_counts(["one", "two"], ["one"])
    insertion = error_counts(["one"], ["one", "two"])
    assert deletion.deletions == 1
    assert insertion.insertions == 1


def test_russian_normalization_is_configurable() -> None:
    assert normalize("Ёлка, один!", fold_yo=True, normalize_numbers=True) == ["елка", "1"]
    assert normalize("Ёлка, один!", fold_yo=False, normalize_numbers=False) == ["ёлка", "один"]
