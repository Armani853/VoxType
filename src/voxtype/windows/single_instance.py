"""One-owner process guard acquired before constructing the desktop runtime."""

from __future__ import annotations

import ctypes
import time
from typing import Any, Protocol

from ._win32 import Win32CallError, clear_last_error, last_error, load_library

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


class SingleInstanceBackend(Protocol):
    def create_mutex(self, name: str) -> tuple[int, bool]: ...

    def close_handle(self, handle: int) -> None: ...

    def activate_window(self, title: str, timeout_s: float) -> bool: ...


class Win32SingleInstanceBackend:
    def create_mutex(self, name: str) -> tuple[int, bool]:
        kernel32 = load_library("kernel32")
        function = kernel32.CreateMutexW
        function.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        function.restype = ctypes.c_void_p
        clear_last_error()
        handle = function(None, 0, name)
        error = last_error()
        if not handle:
            raise Win32CallError("CreateMutexW", error)
        return int(handle), error == ERROR_ALREADY_EXISTS

    def close_handle(self, handle: int) -> None:
        kernel32 = load_library("kernel32")
        function = kernel32.CloseHandle
        function.argtypes = (ctypes.c_void_p,)
        function.restype = ctypes.c_int
        if not function(ctypes.c_void_p(handle)):
            raise Win32CallError("CloseHandle")

    def activate_window(self, title: str, timeout_s: float) -> bool:
        user32 = load_library("user32")
        find_window = user32.FindWindowW
        find_window.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        find_window.restype = ctypes.c_void_p
        show_window = user32.ShowWindow
        show_window.argtypes = (ctypes.c_void_p, ctypes.c_int)
        show_window.restype = ctypes.c_int
        foreground = user32.SetForegroundWindow
        foreground.argtypes = (ctypes.c_void_p,)
        foreground.restype = ctypes.c_int
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            hwnd = find_window(None, title)
            if hwnd:
                show_window(hwnd, SW_RESTORE)
                foreground(hwnd)
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


class SingleInstanceGuard:
    """Own one named local mutex for the full desktop event-loop lifetime."""

    def __init__(
        self,
        *,
        name: str = r"Local\VoxType.Desktop.Singleton.v1",
        window_title: str = "VoxType — локальная диктовка",
        backend: SingleInstanceBackend | None = None,
    ) -> None:
        if not name or "\x00" in name:
            raise ValueError("single-instance mutex name is invalid")
        self._name = name
        self._window_title = window_title
        self._backend = backend or Win32SingleInstanceBackend()
        self._handle: int | None = None
        self._owner = False

    @property
    def is_owner(self) -> bool:
        return self._owner

    def acquire(self) -> bool:
        if self._handle is not None:
            return self._owner
        handle, already_exists = self._backend.create_mutex(self._name)
        if already_exists:
            self._backend.close_handle(handle)
            return False
        self._handle = handle
        self._owner = True
        return True

    def activate_existing(self, timeout_s: float = 3.0) -> bool:
        if self._owner:
            return False
        return self._backend.activate_window(self._window_title, timeout_s)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        self._owner = False
        if handle is not None:
            self._backend.close_handle(handle)

    def __enter__(self) -> SingleInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
