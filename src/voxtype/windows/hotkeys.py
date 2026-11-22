"""Native global hotkeys with conflict reporting and autorepeat suppression."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass, replace
from enum import IntFlag
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol

from ._win32 import clear_last_error, last_error, load_library

if TYPE_CHECKING:
    from voxtype.windows.keyboard_hold import KeyboardHold

WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
VK_F12 = 0x7B


class HotkeyModifiers(IntFlag):
    ALT = 0x0001
    CONTROL = 0x0002
    SHIFT = 0x0004
    WINDOWS = 0x0008
    NO_REPEAT = 0x4000


@dataclass(frozen=True, slots=True)
class Hotkey:
    identifier: int
    name: str
    modifiers: HotkeyModifiers
    virtual_key: int

    @property
    def native_modifiers(self) -> int:
        return int(self.modifiers | HotkeyModifiers.NO_REPEAT)


DEFAULT_HOTKEYS = (
    Hotkey(
        1,
        "toggle_dictation",
        HotkeyModifiers.CONTROL | HotkeyModifiers.ALT,
        0x20,
    ),
    Hotkey(
        2,
        "toggle_language",
        HotkeyModifiers.CONTROL | HotkeyModifiers.ALT,
        ord("L"),
    ),
    Hotkey(
        3,
        "toggle_pause",
        HotkeyModifiers.CONTROL | HotkeyModifiers.ALT,
        ord("P"),
    ),
)


class HotkeyRegistrationError(RuntimeError):
    def __init__(self, hotkey: Hotkey, error_code: int) -> None:
        super().__init__(
            f"Could not register hotkey {format_hotkey(hotkey)} (Win32 error {error_code})"
        )
        self.hotkey = hotkey
        self.error_code = error_code


class HotkeyConflictError(HotkeyRegistrationError):
    """The requested combination is already owned by another application."""


class HotkeyBackend(Protocol):
    def register(
        self, hwnd: int, identifier: int, modifiers: int, virtual_key: int
    ) -> tuple[bool, int]: ...

    def unregister(self, hwnd: int, identifier: int) -> bool: ...


@lru_cache(maxsize=1)
def _configure_api() -> Any:
    user32 = load_library("user32")
    user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    return user32


class NativeHotkeyBackend:
    def register(
        self, hwnd: int, identifier: int, modifiers: int, virtual_key: int
    ) -> tuple[bool, int]:
        user32 = _configure_api()
        clear_last_error()
        succeeded = bool(
            user32.RegisterHotKey(wintypes.HWND(hwnd), identifier, modifiers, virtual_key)
        )
        return succeeded, 0 if succeeded else last_error()

    def unregister(self, hwnd: int, identifier: int) -> bool:
        user32 = _configure_api()
        return bool(user32.UnregisterHotKey(wintypes.HWND(hwnd), identifier))


_NAMED_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}

_MODIFIER_NAMES = {
    "alt": HotkeyModifiers.ALT,
    "ctrl": HotkeyModifiers.CONTROL,
    "control": HotkeyModifiers.CONTROL,
    "shift": HotkeyModifiers.SHIFT,
    "win": HotkeyModifiers.WINDOWS,
    "windows": HotkeyModifiers.WINDOWS,
}


def virtual_key_from_name(name: str) -> int:
    normalized = name.strip().casefold()
    if normalized in _NAMED_KEYS:
        return _NAMED_KEYS[normalized]
    if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
        return ord(normalized.upper())
    if normalized.startswith("f") and normalized[1:].isdigit():
        number = int(normalized[1:])
        if 1 <= number <= 24:
            return 0x6F + number
    raise ValueError(f"Unsupported hotkey key: {name!r}")


def parse_hotkey(specification: str, *, identifier: int, name: str) -> Hotkey:
    parts = [part.strip() for part in specification.split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("A global hotkey must include a modifier and a key")
    modifiers = HotkeyModifiers(0)
    key_parts: list[str] = []
    for part in parts:
        modifier = _MODIFIER_NAMES.get(part.casefold())
        if modifier is None:
            key_parts.append(part)
        else:
            modifiers |= modifier
    if len(key_parts) != 1 or not modifiers:
        raise ValueError("Hotkey must contain exactly one non-modifier key")
    hotkey = Hotkey(identifier, name, modifiers, virtual_key_from_name(key_parts[0]))
    validate_hotkey(hotkey)
    return hotkey


def validate_hotkey(hotkey: Hotkey, *, allow_windows_key: bool = False) -> None:
    if not 0 <= hotkey.identifier <= 0xBFFF:
        raise ValueError("Application hotkey id must be in the 0x0000..0xBFFF range")
    if not hotkey.name:
        raise ValueError("Hotkey name cannot be empty")
    if hotkey.virtual_key == VK_F12:
        raise ValueError("F12 is permanently reserved for the debugger")
    if hotkey.virtual_key <= 0 or hotkey.virtual_key > 0xFF:
        raise ValueError("Virtual-key code must be in the 1..255 range")
    effective = hotkey.modifiers & ~HotkeyModifiers.NO_REPEAT
    if not effective:
        raise ValueError("A global hotkey must include at least one modifier")
    if effective & HotkeyModifiers.WINDOWS and not allow_windows_key:
        raise ValueError("Windows-key shortcuts are reserved by the operating system")
    if effective == HotkeyModifiers.ALT and hotkey.virtual_key in {0x09, 0x73}:
        raise ValueError("This system shortcut cannot be registered")


def format_hotkey(hotkey: Hotkey) -> str:
    labels: list[str] = []
    for modifier, label in (
        (HotkeyModifiers.CONTROL, "Ctrl"),
        (HotkeyModifiers.ALT, "Alt"),
        (HotkeyModifiers.SHIFT, "Shift"),
        (HotkeyModifiers.WINDOWS, "Win"),
    ):
        if hotkey.modifiers & modifier:
            labels.append(label)
    reverse = {value: key.title() for key, value in _NAMED_KEYS.items()}
    if ord("A") <= hotkey.virtual_key <= ord("Z") or ord("0") <= hotkey.virtual_key <= ord("9"):
        key = chr(hotkey.virtual_key)
    elif 0x70 <= hotkey.virtual_key <= 0x87:
        key = f"F{hotkey.virtual_key - 0x6F}"
    else:
        key = reverse.get(hotkey.virtual_key, f"VK_{hotkey.virtual_key:02X}")
    return "+".join((*labels, key))


HotkeyCallback = Callable[[Hotkey], None]


class GlobalHotkeyManager:
    """Own registrations for one message-loop HWND and dispatch WM_HOTKEY."""

    def __init__(self, hwnd: int = 0, backend: HotkeyBackend | None = None) -> None:
        self._hwnd = int(hwnd)
        self._backend = backend or NativeHotkeyBackend()
        self._hold: KeyboardHold | None = None
        self._hold_spec: Hotkey | None = None
        self._registered: dict[int, Hotkey] = {}
        self._callbacks: dict[int, HotkeyCallback] = {}
        self._lock = threading.RLock()

    @property
    def registered(self) -> tuple[Hotkey, ...]:
        with self._lock:
            return tuple(self._registered.values())

    def register(self, hotkey: Hotkey, callback: HotkeyCallback | None = None) -> None:
        validate_hotkey(hotkey)
        with self._lock:
            if hotkey.identifier in self._registered:
                raise ValueError(f"Hotkey id {hotkey.identifier} is already registered")
            combination = (int(hotkey.modifiers & ~HotkeyModifiers.NO_REPEAT), hotkey.virtual_key)
            if any(
                (
                    int(item.modifiers & ~HotkeyModifiers.NO_REPEAT),
                    item.virtual_key,
                )
                == combination
                for item in self._registered.values()
            ):
                raise ValueError("This hotkey combination is already registered by VoxType")
            succeeded, error_code = self._backend.register(
                self._hwnd,
                hotkey.identifier,
                hotkey.native_modifiers,
                hotkey.virtual_key,
            )
            if not succeeded:
                error_type = (
                    HotkeyConflictError
                    if error_code == ERROR_HOTKEY_ALREADY_REGISTERED
                    else HotkeyRegistrationError
                )
                raise error_type(hotkey, error_code)
            self._registered[hotkey.identifier] = hotkey
            if callback is not None:
                self._callbacks[hotkey.identifier] = callback

    def register_many(
        self,
        hotkeys: Iterable[Hotkey],
        callbacks: dict[str, HotkeyCallback] | None = None,
    ) -> None:
        newly_registered: list[int] = []
        try:
            for hotkey in hotkeys:
                callback = None if callbacks is None else callbacks.get(hotkey.name)
                self.register(hotkey, callback)
                newly_registered.append(hotkey.identifier)
        except (ValueError, HotkeyRegistrationError):
            for identifier in reversed(newly_registered):
                self.unregister(identifier)
            raise

    def replace_all(
        self,
        hotkeys: Iterable[Hotkey],
        callbacks: dict[str, HotkeyCallback],
    ) -> None:
        """Acquire new combinations before moving callbacks off the old ones."""

        requested = tuple(hotkeys)

        def key(item: Hotkey) -> tuple[int, int]:
            return int(item.modifiers & ~HotkeyModifiers.NO_REPEAT), item.virtual_key

        if len({key(item) for item in requested}) != len(requested):
            raise ValueError("Одна комбинация назначена нескольким действиям")
        with self._lock:
            existing = {key(item): item for item in self._registered.values()}
            previous_callbacks = dict(self._callbacks)
            selected: list[Hotkey] = []
            acquired: list[int] = []
            released: list[Hotkey] = []
            try:
                for item in requested:
                    validate_hotkey(item)
                    old = existing.get(key(item))
                    identifier = (
                        old.identifier
                        if old is not None
                        else next(
                            number
                            for number in range(0x100, 0xBFFF)
                            if number not in self._registered
                        )
                    )
                    bound = replace(item, identifier=identifier)
                    if old is None:
                        self.register(bound)
                        acquired.append(identifier)
                    selected.append(bound)
                used = {item.identifier for item in selected}
                for old in existing.values():
                    if old.identifier not in used:
                        if not self.unregister(old.identifier):
                            raise RuntimeError("Не удалось освободить прежнее сочетание")
                        released.append(old)
            except (ValueError, OSError, RuntimeError):
                for identifier in reversed(acquired):
                    self.unregister(identifier)
                for old in released:
                    self.register(old, previous_callbacks.get(old.identifier))
                raise
            self._registered.update({item.identifier: item for item in selected})
            self._callbacks = {
                item.identifier: callbacks[item.name] for item in selected if item.name in callbacks
            }

    def unregister(self, identifier: int) -> bool:
        with self._lock:
            if identifier not in self._registered:
                return False
            succeeded = self._backend.unregister(self._hwnd, identifier)
            if succeeded:
                self._registered.pop(identifier, None)
                self._callbacks.pop(identifier, None)
            return succeeded

    def unregister_all(self) -> tuple[int, ...]:
        failures: list[int] = []
        with self._lock:
            identifiers = tuple(self._registered)
        for identifier in identifiers:
            if not self.unregister(identifier):
                failures.append(identifier)
        return tuple(failures)

    def handle_message(self, message: int, wparam: int, lparam: int = 0) -> Hotkey | None:
        del lparam
        if message != WM_HOTKEY:
            return None
        with self._lock:
            hotkey = self._registered.get(int(wparam))
            callback = self._callbacks.get(int(wparam))
        if hotkey is not None and callback is not None:
            callback(hotkey)
        return hotkey

    def replace_gestures(
        self,
        hotkeys: Iterable[Hotkey],
        callbacks: dict[str, HotkeyCallback],
        hold: Hotkey | None,
        changed: Callable[[bool], None],
    ) -> None:
        with self._lock:
            old = tuple(self._registered.values())
            old_callbacks = {
                item.name: self._callbacks[item.identifier]
                for item in old
                if item.identifier in self._callbacks
            }
            self.replace_all(hotkeys, callbacks)
            try:
                self.configure_hold(hold, changed)
            except (ValueError, OSError, RuntimeError):
                self.replace_all(old, old_callbacks)
                raise

    @property
    def hold_active(self) -> bool:
        return self._hold is not None

    def release_hold_state(self) -> None:
        if self._hold is not None:
            self._hold.reset()

    def configure_hold(self, hotkey: Hotkey | None, changed: Callable[[bool], None]) -> None:
        if hotkey is not None:
            validate_hotkey(hotkey)
            if any(
                item.virtual_key == hotkey.virtual_key and item.modifiers == hotkey.modifiers
                for item in self.registered
            ):
                raise ValueError("Push-to-talk conflicts with a registered VoxType hotkey")
        if self._hold_spec == hotkey:
            return
        replacement = None
        if hotkey is not None:
            from voxtype.windows.keyboard_hold import KeyboardHold

            replacement = KeyboardHold(hotkey.virtual_key, hotkey.native_modifiers, changed)
        try:
            if self._hold is not None:
                self._hold.close()
        except OSError:
            if replacement is not None:
                replacement.close()
            raise
        self._hold = replacement
        self._hold_spec = hotkey

    def close(self) -> tuple[int, ...]:
        if self._hold is not None:
            self._hold.close()
            self._hold = None
        return self.unregister_all()

    def __enter__(self) -> GlobalHotkeyManager:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
