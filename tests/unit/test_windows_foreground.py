from datetime import UTC

from voxtype.windows.foreground import (
    IntegrityComparison,
    IntegrityLevel,
    WindowMetadata,
    compare_integrity,
)


def test_integrity_rids_are_classified_by_mandatory_label_band() -> None:
    assert IntegrityLevel.from_rid(0x0000) is IntegrityLevel.UNTRUSTED
    assert IntegrityLevel.from_rid(0x1001) is IntegrityLevel.LOW
    assert IntegrityLevel.from_rid(0x2000) is IntegrityLevel.MEDIUM
    assert IntegrityLevel.from_rid(0x2100) is IntegrityLevel.MEDIUM_PLUS
    assert IntegrityLevel.from_rid(0x3000) is IntegrityLevel.HIGH
    assert IntegrityLevel.from_rid(0x5001) is IntegrityLevel.PROTECTED
    assert IntegrityLevel.from_rid(-1) is IntegrityLevel.UNKNOWN


def test_uipi_comparison_is_conservative_for_unknown_integrity() -> None:
    assert (
        compare_integrity(IntegrityLevel.HIGH, IntegrityLevel.MEDIUM)
        is IntegrityComparison.TARGET_HIGHER
    )
    assert (
        compare_integrity(IntegrityLevel.MEDIUM, IntegrityLevel.MEDIUM) is IntegrityComparison.SAFE
    )
    assert (
        compare_integrity(IntegrityLevel.UNKNOWN, IntegrityLevel.MEDIUM)
        is IntegrityComparison.UNKNOWN
    )


def test_window_metadata_has_stable_hwnd_pid_identity_and_utc_timestamp() -> None:
    metadata = WindowMetadata(
        hwnd=123,
        title="Editor",
        process_id=456,
        process_name="editor.exe",
        integrity_level=IntegrityLevel.MEDIUM,
    )
    assert metadata.identity == (123, 456)
    assert metadata.captured_at.tzinfo is UTC
    assert not metadata.is_elevated
