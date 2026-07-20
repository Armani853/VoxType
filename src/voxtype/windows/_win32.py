"""Small, import-safe helpers shared by the native Windows adapters."""

from __future__ import annotations

import ctypes
import os
from functools import cache
from typing import Any


class WindowsUnavailableError(RuntimeError):
    """Raised when a native Windows operation is requested on another OS."""


class Win32CallError(OSError):
    """An error reported by a Win32 API call."""

    def __init__(self, function: str, error_code: int | None = None) -> None:
        code = last_error() if error_code is None else error_code
        try:
            message = ctypes.FormatError(code).strip()
        except (AttributeError, OSError):
            message = "Win32 call failed"
        super().__init__(code, f"{function}: {message}")
        self.function = function
        self.error_code = code


def require_windows() -> None:
    """Fail clearly at call time while keeping module imports cross-platform."""

    if os.name != "nt":
        raise WindowsUnavailableError("This operation is available only on Windows")


@cache
def load_library(name: str) -> Any:
    """Load a Windows DLL lazily so importing VoxType remains portable."""

    require_windows()
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise WindowsUnavailableError("ctypes.WinDLL is unavailable")
    return loader(name, use_last_error=True)


def last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def clear_last_error() -> None:
    setter = getattr(ctypes, "set_last_error", None)
    if setter is not None:
        setter(0)
