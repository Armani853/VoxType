from collections.abc import Callable

import pytest

from voxtype.windows.clipboard import (
    CF_UNICODETEXT,
    ClipboardItem,
    ClipboardManager,
    MemoryClipboardBackend,
)
from voxtype.windows.foreground import IntegrityLevel, WindowMetadata
from voxtype.windows.injection import (
    DeliveryStatus,
    InjectionStatus,
    InjectionStrategy,
    PendingTextQueue,
    TextInjectionService,
    join_fragments,
    prepare_fragment,
)
from voxtype.windows.send_input import ModifierStillPressedError, SendInputError
from voxtype.windows.target_window import TargetDecision, TargetStatus


def unicode_item(text: str) -> ClipboardItem:
    return ClipboardItem(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")


TARGET = WindowMetadata(
    100,
    "Editor",
    200,
    "editor.exe",
    IntegrityLevel.MEDIUM,
)
OTHER_TARGET = WindowMetadata(
    101,
    "Other editor",
    201,
    "other.exe",
    IntegrityLevel.MEDIUM,
)


def decision(status: TargetStatus, target: WindowMetadata | None = TARGET) -> TargetDecision:
    return TargetDecision(status, target, target, status.value)


class Resolver:
    def __init__(self, current: TargetDecision) -> None:
        self.current = current
        self.sequence: list[TargetDecision] = []

    def resolve(self, *, foreground: WindowMetadata | None = None) -> TargetDecision:
        del foreground
        if self.sequence:
            return self.sequence.pop(0)
        return self.current


class RecordingSender:
    def __init__(self, clipboard: ClipboardManager) -> None:
        self.clipboard = clipboard
        self.pasted: list[str | None] = []
        self.unicode: list[str] = []
        self.paste_error: SendInputError | None = None
        self.on_paste: Callable[[], None] | None = None

    def send_paste_shortcut(self) -> int:
        self.pasted.append(self.clipboard.get_text())
        if self.on_paste is not None:
            self.on_paste()
        if self.paste_error is not None:
            raise self.paste_error
        return 4

    def send_unicode(self, text: str) -> int:
        self.unicode.append(text)
        return len(text) * 2


def build_service(
    resolver: Resolver,
    *,
    original: list[ClipboardItem] | None = None,
) -> tuple[TextInjectionService, MemoryClipboardBackend, RecordingSender]:
    backend = MemoryClipboardBackend(original or [unicode_item("original")])
    clipboard = ClipboardManager(backend)
    sender = RecordingSender(clipboard)
    service = TextInjectionService(
        resolver,
        clipboard=clipboard,
        sender=sender,
        paste_delay=0,
    )
    return service, backend, sender


def test_auto_strategy_pastes_unicode_and_restores_all_original_formats() -> None:
    original = [
        ClipboardItem(0xC111, b"{\\rtf1 keep}", "Rich Text Format"),
        unicode_item("keep"),
    ]
    service, backend, sender = build_service(
        Resolver(decision(TargetStatus.READY)), original=original
    )

    result = service.submit("Привет 😀")

    assert result.status is InjectionStatus.INSERTED
    assert result.strategy is InjectionStrategy.CLIPBOARD
    assert result.clipboard_restored is True
    assert sender.pasted == ["Привет 😀"]
    assert sender.unicode == []
    assert backend.items == original
    assert service.pending_fragments == 0


def test_inactive_locked_target_queues_then_flushes_in_original_order() -> None:
    resolver = Resolver(decision(TargetStatus.TARGET_INACTIVE))
    service, _, sender = build_service(resolver)
    first = service.submit("Hello")
    second = service.submit("world")
    assert first.status is InjectionStatus.QUEUED
    assert second.status is InjectionStatus.QUEUED
    assert service.pending_fragments == 2

    resolver.current = decision(TargetStatus.READY)
    flushed = service.flush()
    assert flushed.status is InjectionStatus.INSERTED
    assert flushed.delivered_text == "Hello world"
    assert sender.pasted == ["Hello", " world"]
    assert service.pending_fragments == 0


def test_spacing_rules_cover_punctuation_double_space_and_newline() -> None:
    assert prepare_fragment("Hello", ",") == ","
    assert prepare_fragment("Hello", "   world") == " world"
    assert prepare_fragment("Hello ", " world") == "world"
    assert prepare_fragment("Hello", "\nworld") == "\nworld"
    assert join_fragments(["Hello", ",", "world", "!", "\n", "Next"]) == ("Hello, world!\nNext")


def test_uipi_block_keeps_text_and_exposes_explicit_safe_copy() -> None:
    resolver = Resolver(decision(TargetStatus.HIGHER_INTEGRITY))
    service, _, sender = build_service(resolver)
    blocked = service.submit("not lost")
    assert blocked.status is InjectionStatus.BLOCKED
    assert blocked.requires_safe_copy
    assert service.pending_fragments == 1
    assert sender.pasted == []

    copied = service.copy_pending_to_clipboard(consume=True)
    assert copied.status is InjectionStatus.COPIED
    assert copied.delivered_text == "not lost"
    assert sender.clipboard.get_text() == "not lost"
    assert service.pending_fragments == 0


def test_auto_falls_back_to_unicode_only_when_paste_sent_nothing() -> None:
    service, backend, sender = build_service(Resolver(decision(TargetStatus.READY)))
    original = list(backend.items)
    sender.paste_error = SendInputError(0, 4)
    result = service.submit("fallback")
    assert result.status is InjectionStatus.INSERTED
    assert result.strategy is InjectionStrategy.UNICODE
    assert sender.unicode == ["fallback"]
    assert backend.items == original


def test_partial_paste_failure_is_not_retried_as_unicode_or_discarded() -> None:
    service, _, sender = build_service(Resolver(decision(TargetStatus.READY)))
    sender.paste_error = SendInputError(2, 4)
    result = service.submit("uncertain")
    assert result.status is InjectionStatus.UNKNOWN
    assert sender.unicode == []
    assert service.pending_fragments == 0


def test_fragment_id_prevents_duplicate_delivery_and_tracks_fifo_status() -> None:
    resolver = Resolver(decision(TargetStatus.TARGET_INACTIVE))
    service, _, sender = build_service(resolver)
    first = service.submit("one", fragment_id="session:1")
    duplicate = service.submit("one", fragment_id="session:1")
    assert first.status is InjectionStatus.QUEUED
    assert duplicate.status is InjectionStatus.DUPLICATE
    assert service.pending_fragments == 1
    assert service.delivery_status("session:1") is DeliveryStatus.QUEUED_TARGET_INACTIVE

    resolver.current = decision(TargetStatus.READY)
    assert service.flush().status is InjectionStatus.INSERTED
    assert sender.pasted == ["one"]
    assert service.delivery_status("session:1") is DeliveryStatus.DELIVERED


def test_pressed_hotkey_modifier_queues_instead_of_triggering_unicode_fallback() -> None:
    service, _, sender = build_service(Resolver(decision(TargetStatus.READY)))
    sender.paste_error = ModifierStillPressedError()
    result = service.submit("wait for key release")
    assert result.status is InjectionStatus.FAILED
    assert sender.unicode == []
    assert service.pending_fragments == 1


def test_incomplete_clipboard_snapshot_uses_unicode_without_mutating_clipboard() -> None:
    original = [ClipboardItem(0xC555, b"large-format-data")]
    backend = MemoryClipboardBackend(original)
    clipboard = ClipboardManager(
        backend,
        max_total_bytes=4,
        max_format_bytes=4,
    )
    sender = RecordingSender(clipboard)
    service = TextInjectionService(
        Resolver(decision(TargetStatus.READY)),
        clipboard=clipboard,
        sender=sender,
        paste_delay=0,
    )
    result = service.submit("safe fallback")
    assert result.status is InjectionStatus.INSERTED
    assert result.strategy is InjectionStrategy.UNICODE
    assert sender.unicode == ["safe fallback"]
    assert sender.pasted == []
    assert backend.items == original


def test_focus_change_during_clipboard_setup_prevents_paste_and_restores() -> None:
    resolver = Resolver(decision(TargetStatus.TARGET_INACTIVE))
    resolver.sequence = [
        decision(TargetStatus.READY),
        decision(TargetStatus.TARGET_INACTIVE),
    ]
    service, backend, sender = build_service(resolver)
    original = list(backend.items)
    result = service.submit("stay queued")
    assert result.status is InjectionStatus.QUEUED
    assert sender.pasted == []
    assert backend.items == original
    assert service.pending_fragments == 1


def test_pending_text_is_never_retargeted_to_a_new_session_window() -> None:
    resolver = Resolver(decision(TargetStatus.TARGET_INACTIVE))
    service, _, sender = build_service(resolver)
    assert service.submit("belongs to first target").status is InjectionStatus.QUEUED

    resolver.current = decision(TargetStatus.READY, OTHER_TARGET)
    result = service.flush()
    assert result.status is InjectionStatus.BLOCKED
    assert result.requires_safe_copy
    assert service.pending_fragments == 1
    assert sender.pasted == []
    assert sender.unicode == []


def test_new_user_clipboard_change_is_never_overwritten_after_paste() -> None:
    service, backend, sender = build_service(Resolver(decision(TargetStatus.READY)))
    newer = [unicode_item("new user copy")]
    sender.on_paste = lambda: backend.simulate_external_change(newer)
    result = service.submit("inserted")
    assert result.status is InjectionStatus.INSERTED
    assert result.clipboard_restored is False
    assert backend.items == newer


def test_safe_copy_strategy_copies_without_synthetic_input() -> None:
    service, _, sender = build_service(Resolver(decision(TargetStatus.READY)))
    result = service.submit("manual", strategy=InjectionStrategy.SAFE_COPY)
    assert result.status is InjectionStatus.COPIED
    assert sender.pasted == []
    assert sender.unicode == []
    assert sender.clipboard.get_text() == "manual"


def test_paste_delay_can_be_updated_after_settings_apply() -> None:
    service, _, _ = build_service(Resolver(decision(TargetStatus.READY)))
    service.paste_delay = 0.35
    assert service.paste_delay == 0.35
    with pytest.raises(ValueError):
        service.paste_delay = -0.1


def test_pending_queue_spills_transparently_and_preserves_unicode_newlines() -> None:
    queue = PendingTextQueue(memory_limit_bytes=1)
    queue.append("первая\nстрока")
    queue.append("😀 second")
    assert tuple(queue) == ("первая\nстрока", "😀 second")
    assert queue.popleft() == "первая\nстрока"
    assert queue.popleft() == "😀 second"
    assert len(queue) == 0
    with pytest.raises(IndexError):
        queue.popleft()
    queue.close()
