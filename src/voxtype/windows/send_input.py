"""Layout-independent Unicode and shortcut input through Win32 SendInput."""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar, Protocol

from ._win32 import clear_last_error, last_error, load_library

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56


class SendInputError(RuntimeError):
    """Raised when Windows accepts fewer input records than requested."""

    def __init__(self, sent: int, expected: int, error_code: int = 0) -> None:
        detail = f"SendInput accepted {sent} of {expected} keyboard events"
        if error_code:
            detail += f" (Win32 error {error_code})"
        else:
            detail += " (UIPI may have blocked injection)"
        super().__init__(detail)
        self.sent = sent
        self.expected = expected
        self.error_code = error_code


class ModifierStillPressedError(SendInputError):
    def __init__(self) -> None:
        super().__init__(0, 4, 0)
        self.args = ("A physical modifier key is still pressed; paste was not sent",)


@dataclass(frozen=True, slots=True)
class KeyStroke:
    virtual_key: int
    scan_code: int
    flags: int


class KeyboardInputBackend(Protocol):
    def send_keyboard(self, events: Sequence[KeyStroke]) -> int: ...

    def is_key_down(self, virtual_key: int) -> bool: ...


_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, type[ctypes.Structure]]]] = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


@lru_cache(maxsize=1)
def _configure_api() -> Any:
    user32 = load_library("user32")
    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(_INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wintypes.SHORT
    return user32


class NativeSendInputBackend:
    def send_keyboard(self, events: Sequence[KeyStroke]) -> int:
        if not events:
            return 0
        user32 = _configure_api()
        native = (_INPUT * len(events))()
        for index, event in enumerate(events):
            native[index].type = INPUT_KEYBOARD
            native[index].ki = _KEYBDINPUT(
                wVk=event.virtual_key,
                wScan=event.scan_code,
                dwFlags=event.flags,
                time=0,
                dwExtraInfo=0,
            )
        clear_last_error()
        return int(user32.SendInput(len(events), native, ctypes.sizeof(_INPUT)))

    def is_key_down(self, virtual_key: int) -> bool:
        user32 = _configure_api()
        return bool(int(user32.GetAsyncKeyState(virtual_key)) & 0x8000)


def utf16_code_units(text: str) -> tuple[int, ...]:
    """Encode text exactly as UTF-16 code units accepted by KEYEVENTF_UNICODE."""

    encoded = text.encode("utf-16-le", errors="strict")
    return tuple(
        int.from_bytes(encoded[index : index + 2], "little") for index in range(0, len(encoded), 2)
    )


def chunk_utf16_units(units: Sequence[int], max_units: int = 64) -> tuple[tuple[int, ...], ...]:
    """Bound chunks without ever splitting or accepting malformed surrogates."""

    if max_units < 1:
        raise ValueError("max_units must be positive")
    tokens: list[tuple[int, ...]] = []
    index = 0
    while index < len(units):
        unit = int(units[index])
        token: tuple[int, ...]
        if not 0 <= unit <= 0xFFFF:
            raise ValueError(f"invalid UTF-16 code unit: {unit}")
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 >= len(units):
                raise ValueError("unpaired high surrogate")
            low = int(units[index + 1])
            if not 0xDC00 <= low <= 0xDFFF:
                raise ValueError("high surrogate is not followed by a low surrogate")
            token = (unit, low)
            index += 2
        elif 0xDC00 <= unit <= 0xDFFF:
            raise ValueError("unpaired low surrogate")
        else:
            token = (unit,)
            index += 1
        if len(token) > max_units:
            raise ValueError("max_units is too small for a surrogate pair")
        tokens.append(token)

    chunks: list[tuple[int, ...]] = []
    current: list[int] = []
    for token in tokens:
        if current and len(current) + len(token) > max_units:
            chunks.append(tuple(current))
            current = []
        current.extend(token)
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def iter_utf16_chunks(text: str, max_units: int = 64) -> tuple[tuple[int, ...], ...]:
    return chunk_utf16_units(utf16_code_units(text), max_units)


def build_unicode_events(units: Sequence[int]) -> tuple[KeyStroke, ...]:
    events: list[KeyStroke] = []
    for unit in units:
        events.append(KeyStroke(0, int(unit), KEYEVENTF_UNICODE))
        events.append(KeyStroke(0, int(unit), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return tuple(events)


def build_ctrl_v_events() -> tuple[KeyStroke, ...]:
    return (
        KeyStroke(VK_CONTROL, 0, 0),
        KeyStroke(VK_V, 0, 0),
        KeyStroke(VK_V, 0, KEYEVENTF_KEYUP),
        KeyStroke(VK_CONTROL, 0, KEYEVENTF_KEYUP),
    )


class SendInputController:
    """Serialize bounded SendInput calls and validate every native result."""

    def __init__(
        self,
        backend: KeyboardInputBackend | None = None,
        *,
        max_chunk_units: int = 64,
        inter_chunk_delay: float = 0.001,
        modifier_release_timeout: float = 0.75,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_chunk_units < 1:
            raise ValueError("max_chunk_units must be positive")
        if inter_chunk_delay < 0 or modifier_release_timeout < 0:
            raise ValueError("input timing values cannot be negative")
        self._backend = backend or NativeSendInputBackend()
        self._max_chunk_units = max_chunk_units
        self._inter_chunk_delay = inter_chunk_delay
        self._modifier_release_timeout = modifier_release_timeout
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.RLock()

    @property
    def backend(self) -> KeyboardInputBackend:
        return self._backend

    def _send_exact(self, events: Sequence[KeyStroke]) -> int:
        sent = self._backend.send_keyboard(events)
        if sent != len(events):
            raise SendInputError(sent, len(events), last_error())
        return sent

    def send_unicode(self, text: str) -> int:
        """Send text in bounded batches; return the number of input records."""

        if not text:
            return 0
        chunks = iter_utf16_chunks(text, self._max_chunk_units)
        total = 0
        with self._lock:
            for index, chunk in enumerate(chunks):
                total += self._send_exact(build_unicode_events(chunk))
                if index + 1 < len(chunks) and self._inter_chunk_delay:
                    self._sleep(self._inter_chunk_delay)
        return total

    def wait_for_modifiers_released(self) -> bool:
        modifiers = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
        deadline = self._monotonic() + self._modifier_release_timeout
        while any(self._backend.is_key_down(key) for key in modifiers):
            if self._monotonic() >= deadline:
                return False
            self._sleep(min(0.01, max(0.0, deadline - self._monotonic())))
        return True

    def send_paste_shortcut(self) -> int:
        """Send Ctrl+V only after physical hotkey modifiers are released."""

        with self._lock:
            if not self.wait_for_modifiers_released():
                raise ModifierStillPressedError
            return self._send_exact(build_ctrl_v_events())


def send_unicode_text(text: str, *, max_chunk_units: int = 64) -> int:
    return SendInputController(max_chunk_units=max_chunk_units).send_unicode(text)


def send_ctrl_v() -> int:
    return SendInputController().send_paste_shortcut()
