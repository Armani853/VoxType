"""A Windows-only, opt-in press/release hook for exactly one configured chord."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import ClassVar

from voxtype.windows._win32 import Win32CallError, clear_last_error, last_error, load_library


class _KeyboardEvent(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.DWORD),
        ("scan", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class KeyboardHold:
    _MODIFIERS: ClassVar[dict[int, tuple[int, ...]]] = {
        0x0001: (0x12, 0xA4, 0xA5),  # Alt
        0x0002: (0x11, 0xA2, 0xA3),  # Ctrl
        0x0004: (0x10, 0xA0, 0xA1),  # Shift
        0x0008: (0x5B, 0x5C),  # Win
    }

    def __init__(self, key: int, modifiers: int, changed: Callable[[bool], None]) -> None:
        self._user32 = load_library("user32")
        user32 = self._user32
        self._changed = changed
        self._key = key
        self._required = modifiers & 0x000F
        self._held = False
        self._consume_key_up = False
        self._handle = None
        get_state = user32.GetAsyncKeyState
        get_state.argtypes = (ctypes.c_int,)
        get_state.restype = ctypes.c_short
        self._get_state = get_state
        prototype = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
        )
        self._callback = prototype(self._handle_event)
        install = user32.SetWindowsHookExW
        install.argtypes = (ctypes.c_int, prototype, wintypes.HMODULE, wintypes.DWORD)
        install.restype = wintypes.HANDLE
        module = load_library("kernel32").GetModuleHandleW
        module.argtypes = (wintypes.LPCWSTR,)
        module.restype = wintypes.HMODULE
        user32.CallNextHookEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._handle = install(13, self._callback, module(None), 0)  # WH_KEYBOARD_LL
        if not self._handle:
            raise OSError("Could not install the push-to-talk keyboard hook")

    def _handle_event(self, code: int, message: int, address: int) -> int:
        if code >= 0 and message in {0x100, 0x101, 0x104, 0x105}:
            key = _KeyboardEvent.from_address(address)
            released = message in {0x101, 0x105}
            if not key.flags & 0x10:  # Ignore input synthesized by VoxType and other programs.
                if (
                    released
                    and self._held
                    and (
                        key.vk == self._key
                        or any(
                            mask & self._required and key.vk in keys
                            for mask, keys in self._MODIFIERS.items()
                        )
                    )
                ):
                    self._held = False
                    self._changed(False)
                if key.vk == self._key:
                    if released and self._consume_key_up:
                        self._consume_key_up = False
                        return 1
                    if not released and self._consume_key_up:
                        return 1
                    if not released:
                        active = sum(
                            mask
                            for mask, keys in self._MODIFIERS.items()
                            if any(self._get_state(vk) & 0x8000 for vk in keys)
                        )
                        if active == self._required:
                            self._held = True
                            self._consume_key_up = True
                            self._changed(True)
                            return 1
        return int(self._user32.CallNextHookEx(None, code, message, address))

    def close(self) -> None:
        if self._handle:
            clear_last_error()
            if not self._user32.UnhookWindowsHookEx(self._handle):
                error = last_error()
                if error != 1404:  # ERROR_INVALID_HOOK_HANDLE: Windows already removed it.
                    raise Win32CallError("UnhookWindowsHookEx", error)
            self._handle = None
        self.reset()

    def reset(self) -> None:
        self._consume_key_up = False
        if self._held:
            self._held = False
            self._changed(False)
