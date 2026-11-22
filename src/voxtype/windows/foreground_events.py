"""Observe foreground changes on the existing Windows message loop."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from time import monotonic


class ForegroundEvents:
    def __init__(
        self,
        notify: Callable[[int], None],
        *,
        focus_notify: Callable[[], None] | None = None,
    ) -> None:
        self._hook = None
        self._focus_hook = None
        self._last_focus_event = 0.0
        if sys.platform != "win32":
            return

        callback_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        install = user32.SetWinEventHook
        install.argtypes = (
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        install.restype = wintypes.HANDLE
        self._remove = user32.UnhookWinEvent
        self._remove.argtypes = (wintypes.HANDLE,)
        self._remove.restype = wintypes.BOOL

        def callback(
            _hook: int | None,
            event: int,
            hwnd: int | None,
            _object: int,
            _child: int,
            _thread: int,
            _time: int,
        ) -> None:
            if not hwnd:
                return
            if event == 0x0003:
                notify(int(hwnd))
            elif focus_notify is not None:
                now = monotonic()
                if now - self._last_focus_event >= 0.35:
                    self._last_focus_event = now
                    focus_notify()

        native_callback = callback_type(callback)
        self._callback = native_callback
        foreground_event = 0x0003
        # Out-of-context events are delivered to the installing thread.
        self._hook = install(foreground_event, foreground_event, None, native_callback, 0, 0, 0)
        if focus_notify is not None:
            focus_event = 0x8005  # EVENT_OBJECT_FOCUS
            self._focus_hook = install(focus_event, focus_event, None, native_callback, 0, 0, 0)

    @property
    def active(self) -> bool:
        return bool(self._hook)

    def close(self) -> None:
        if self._focus_hook and self._remove(self._focus_hook):
            self._focus_hook = None
        if self._hook and self._remove(self._hook):
            self._hook = None
