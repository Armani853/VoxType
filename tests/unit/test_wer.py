from tools.evaluate_wer import error_counts, normalize, resolve_profile

from voxtype.asr.profiles import ASRProfile, get_profile


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


def test_wer_profiles_use_the_production_asr_registry() -> None:
    for profile in ASRProfile:
        assert resolve_profile(profile.value) == get_profile(profile)

    assert resolve_profile("fast").model_name == "small"
    assert resolve_profile("balanced").model_name == "small"
    assert resolve_profile("maximum_accuracy").model_name == "large-v3-turbo"
