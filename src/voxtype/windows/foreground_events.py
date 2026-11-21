"""Observe foreground changes on the existing Windows message loop."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes


class ForegroundEvents:
    def __init__(self, notify: Callable[[int], None]) -> None:
        self._hook = None
        if sys.platform != "win32":
            return

        callback_type = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
        )
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        install = user32.SetWinEventHook
        install.argtypes = (
            wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        )
        install.restype = wintypes.HANDLE
        self._remove = user32.UnhookWinEvent
        self._remove.argtypes = (wintypes.HANDLE,)
        self._remove.restype = wintypes.BOOL

        @callback_type
        def callback(_hook, _event, hwnd, _object, _child, _thread, _time):
            if hwnd:
                notify(int(hwnd))

        self._callback = callback
        foreground_event = 0x0003
        # Out-of-context events are delivered to the installing thread.
        self._hook = install(foreground_event, foreground_event, None, callback, 0, 0, 0)

    @property
    def active(self) -> bool:
        return bool(self._hook)

    def close(self) -> None:
        if self._hook and self._remove(self._hook):
            self._hook = None
