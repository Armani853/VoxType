import pytest

from voxtype.windows.send_input import (
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    VK_CONTROL,
    VK_V,
    KeyStroke,
    ModifierStillPressedError,
    SendInputController,
    SendInputError,
    build_ctrl_v_events,
    chunk_utf16_units,
    utf16_code_units,
)


class RecordingBackend:
    def __init__(self) -> None:
        self.batches: list[tuple[KeyStroke, ...]] = []
        self.down: set[int] = set()
        self.return_count: int | None = None

    def send_keyboard(self, events: tuple[KeyStroke, ...]) -> int:
        self.batches.append(tuple(events))
        return len(events) if self.return_count is None else self.return_count

    def is_key_down(self, virtual_key: int) -> bool:
        return virtual_key in self.down


def test_utf16_encoding_exposes_surrogate_pair_for_supplementary_character() -> None:
    units = utf16_code_units("A😀Б")
    assert units == (0x0041, 0xD83D, 0xDE00, 0x0411)


def test_chunking_never_splits_surrogate_pairs() -> None:
    units = utf16_code_units("AB😀CD")
    assert chunk_utf16_units(units, 3) == (
        (0x0041, 0x0042),
        (0xD83D, 0xDE00, 0x0043),
        (0x0044,),
    )
    with pytest.raises(ValueError, match="surrogate pair"):
        chunk_utf16_units(utf16_code_units("😀"), 1)


@pytest.mark.parametrize(
    "units",
    [(0xD800,), (0xDC00,), (0xD800, 0x0041), (0x10000,)],
)
def test_chunking_rejects_malformed_code_units(units: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        chunk_utf16_units(units)


def test_unicode_send_is_bounded_and_has_keydown_keyup_for_every_unit() -> None:
    backend = RecordingBackend()
    controller = SendInputController(
        backend,
        max_chunk_units=2,
        inter_chunk_delay=0,
    )
    sent = controller.send_unicode("A😀Б")

    assert sent == 8
    assert [len(batch) for batch in backend.batches] == [2, 4, 2]
    flattened = [event for batch in backend.batches for event in batch]
    assert [event.scan_code for event in flattened[::2]] == [
        0x0041,
        0xD83D,
        0xDE00,
        0x0411,
    ]
    assert all(event.flags == KEYEVENTF_UNICODE for event in flattened[::2])
    assert all(event.flags == KEYEVENTF_UNICODE | KEYEVENTF_KEYUP for event in flattened[1::2])


def test_partial_native_result_raises_instead_of_claiming_success() -> None:
    backend = RecordingBackend()
    backend.return_count = 1
    controller = SendInputController(backend, inter_chunk_delay=0)
    with pytest.raises(SendInputError) as error:
        controller.send_unicode("text")
    assert error.value.sent == 1
    assert error.value.expected == 8


def test_ctrl_v_sequence_is_ordered_and_uses_keyup_flags() -> None:
    events = build_ctrl_v_events()
    assert events == (
        KeyStroke(VK_CONTROL, 0, 0),
        KeyStroke(VK_V, 0, 0),
        KeyStroke(VK_V, 0, KEYEVENTF_KEYUP),
        KeyStroke(VK_CONTROL, 0, KEYEVENTF_KEYUP),
    )


def test_paste_is_not_sent_while_physical_modifier_remains_down() -> None:
    backend = RecordingBackend()
    backend.down.add(VK_CONTROL)
    controller = SendInputController(
        backend,
        modifier_release_timeout=0,
        inter_chunk_delay=0,
    )
    with pytest.raises(ModifierStillPressedError):
        controller.send_paste_shortcut()
    assert backend.batches == []
