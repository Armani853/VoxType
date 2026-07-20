from voxtype.windows.clipboard import (
    CF_UNICODETEXT,
    ClipboardItem,
    ClipboardManager,
    MemoryClipboardBackend,
)


def unicode_item(text: str) -> ClipboardItem:
    return ClipboardItem(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")


def test_snapshot_and_restore_preserve_multiple_formats_and_order() -> None:
    rtf = ClipboardItem(0xC101, b"{\\rtf1 original}", "Rich Text Format")
    html = ClipboardItem(0xC102, b"Version:1.0\r\n<html>original</html>", "HTML Format")
    original = [rtf, html, unicode_item("исходный текст")]
    backend = MemoryClipboardBackend(original)
    clipboard = ClipboardManager(backend)

    snapshot = clipboard.snapshot()
    sequence = clipboard.set_text(
        "временный text 😀", expected_sequence_number=snapshot.sequence_number
    )
    assert clipboard.get_text() == "временный text 😀"
    assert clipboard.restore(snapshot, expected_sequence_number=sequence)
    assert backend.items == original


def test_temporary_text_restores_on_exception() -> None:
    original = [unicode_item("keep me")]
    backend = MemoryClipboardBackend(original)
    clipboard = ClipboardManager(backend)
    lease = None

    try:
        with clipboard.temporary_text("temporary") as active_lease:
            lease = active_lease
            assert clipboard.get_text() == "temporary"
            raise RuntimeError("simulated paste failure")
    except RuntimeError:
        pass

    assert lease is not None and lease.restored is True
    assert backend.items == original


def test_restore_does_not_overwrite_a_newer_user_clipboard_change() -> None:
    original = [unicode_item("original")]
    newer = [unicode_item("new user copy")]
    backend = MemoryClipboardBackend(original)
    clipboard = ClipboardManager(backend)
    snapshot = clipboard.snapshot()
    temporary_sequence = clipboard.set_text(
        "dictation", expected_sequence_number=snapshot.sequence_number
    )

    backend.simulate_external_change(newer)
    assert not clipboard.restore(snapshot, expected_sequence_number=temporary_sequence)
    assert backend.items == newer


def test_snapshot_limits_report_formats_that_cannot_be_preserved() -> None:
    small = ClipboardItem(100, b"1234")
    large = ClipboardItem(101, b"x" * 20)
    clipboard = ClipboardManager(
        MemoryClipboardBackend([small, large]),
        max_total_bytes=8,
        max_format_bytes=8,
    )
    snapshot = clipboard.snapshot()
    assert snapshot.items == (small,)
    assert snapshot.skipped_formats == (101,)
    assert not snapshot.complete


def test_private_text_adds_history_and_cloud_opt_out_formats() -> None:
    backend = MemoryClipboardBackend()
    clipboard = ClipboardManager(backend)
    clipboard.copy_text("local only")

    assert clipboard.get_text() == "local only"
    assert len(backend.items) == 4
    assert backend.items[0].format_id == CF_UNICODETEXT


def test_empty_clipboard_can_be_restored() -> None:
    backend = MemoryClipboardBackend()
    clipboard = ClipboardManager(backend)
    snapshot = clipboard.snapshot()
    sequence = clipboard.set_text("temporary")
    assert clipboard.restore(snapshot, expected_sequence_number=sequence)
    assert backend.items == []
