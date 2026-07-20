import pytest

from voxtype.windows.hotkeys import (
    DEFAULT_HOTKEYS,
    ERROR_HOTKEY_ALREADY_REGISTERED,
    WM_HOTKEY,
    GlobalHotkeyManager,
    Hotkey,
    HotkeyConflictError,
    HotkeyModifiers,
    format_hotkey,
    parse_hotkey,
)


class FakeHotkeyBackend:
    def __init__(self) -> None:
        self.registered: dict[int, tuple[int, int, int]] = {}
        self.conflicting_keys: set[int] = set()
        self.unregister_failures: set[int] = set()

    def register(
        self, hwnd: int, identifier: int, modifiers: int, virtual_key: int
    ) -> tuple[bool, int]:
        if virtual_key in self.conflicting_keys:
            return False, ERROR_HOTKEY_ALREADY_REGISTERED
        self.registered[identifier] = (hwnd, modifiers, virtual_key)
        return True, 0

    def unregister(self, hwnd: int, identifier: int) -> bool:
        del hwnd
        if identifier in self.unregister_failures:
            return False
        return self.registered.pop(identifier, None) is not None


def test_parse_and_format_configurable_hotkey() -> None:
    hotkey = parse_hotkey("Ctrl+Alt+Space", identifier=9, name="toggle")
    assert hotkey.modifiers == HotkeyModifiers.CONTROL | HotkeyModifiers.ALT
    assert hotkey.virtual_key == 0x20
    assert format_hotkey(hotkey) == "Ctrl+Alt+Space"


def test_registration_always_adds_no_repeat() -> None:
    backend = FakeHotkeyBackend()
    manager = GlobalHotkeyManager(123, backend)
    manager.register(DEFAULT_HOTKEYS[0])
    _, native_modifiers, _ = backend.registered[1]
    assert native_modifiers & HotkeyModifiers.NO_REPEAT


def test_conflict_is_reported_without_crashing_or_recording_registration() -> None:
    backend = FakeHotkeyBackend()
    backend.conflicting_keys.add(ord("L"))
    manager = GlobalHotkeyManager(123, backend)
    with pytest.raises(HotkeyConflictError) as error:
        manager.register(DEFAULT_HOTKEYS[1])
    assert error.value.error_code == ERROR_HOTKEY_ALREADY_REGISTERED
    assert manager.registered == ()


def test_register_many_rolls_back_earlier_items_after_conflict() -> None:
    backend = FakeHotkeyBackend()
    backend.conflicting_keys.add(DEFAULT_HOTKEYS[1].virtual_key)
    manager = GlobalHotkeyManager(123, backend)
    with pytest.raises(HotkeyConflictError):
        manager.register_many(DEFAULT_HOTKEYS)
    assert manager.registered == ()
    assert backend.registered == {}


def test_wm_hotkey_dispatches_named_callback_once() -> None:
    backend = FakeHotkeyBackend()
    manager = GlobalHotkeyManager(123, backend)
    received: list[str] = []
    manager.register(DEFAULT_HOTKEYS[0], lambda hotkey: received.append(hotkey.name))

    assert manager.handle_message(0x1234, 1) is None
    assert manager.handle_message(WM_HOTKEY, 1) == DEFAULT_HOTKEYS[0]
    assert received == ["toggle_dictation"]


def test_reserved_and_system_shortcuts_are_rejected() -> None:
    with pytest.raises(ValueError, match="F12"):
        parse_hotkey("Ctrl+F12", identifier=1, name="bad")
    with pytest.raises(ValueError, match="operating system"):
        parse_hotkey("Win+L", identifier=1, name="bad")
    with pytest.raises(ValueError, match="modifier"):
        parse_hotkey("A", identifier=1, name="bad")


def test_duplicate_id_and_duplicate_combination_are_rejected_locally() -> None:
    backend = FakeHotkeyBackend()
    manager = GlobalHotkeyManager(0, backend)
    first = DEFAULT_HOTKEYS[0]
    manager.register(first)
    with pytest.raises(ValueError, match="id"):
        manager.register(Hotkey(first.identifier, "other", HotkeyModifiers.ALT, ord("X")))
    with pytest.raises(ValueError, match="combination"):
        manager.register(Hotkey(22, "other", first.modifiers, first.virtual_key))
