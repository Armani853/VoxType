"""Capture stable metadata about foreground and target windows."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._win32 import load_library, require_windows

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25


class IntegrityLevel(IntEnum):
    """Mandatory integrity RID values used by Windows UIPI."""

    UNKNOWN = -1
    UNTRUSTED = 0x0000
    LOW = 0x1000
    MEDIUM = 0x2000
    MEDIUM_PLUS = 0x2100
    HIGH = 0x3000
    SYSTEM = 0x4000
    PROTECTED = 0x5000

    @classmethod
    def from_rid(cls, rid: int) -> IntegrityLevel:
        if rid < 0:
            return cls.UNKNOWN
        if rid >= cls.PROTECTED:
            return cls.PROTECTED
        if rid >= cls.SYSTEM:
            return cls.SYSTEM
        if rid >= cls.HIGH:
            return cls.HIGH
        if rid >= cls.MEDIUM_PLUS:
            return cls.MEDIUM_PLUS
        if rid >= cls.MEDIUM:
            return cls.MEDIUM
        if rid >= cls.LOW:
            return cls.LOW
        return cls.UNTRUSTED


class IntegrityComparison(StrEnum):
    SAFE = "safe"
    TARGET_HIGHER = "target_higher"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WindowMetadata:
    """Identity and security information captured for a top-level window."""

    hwnd: int
    title: str
    process_id: int
    process_name: str
    integrity_level: IntegrityLevel
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    process_path: str | None = None

    @property
    def identity(self) -> tuple[int, int]:
        """HWND plus PID guards against a destroyed handle being reused."""

        return (self.hwnd, self.process_id)

    @property
    def is_elevated(self) -> bool:
        return self.integrity_level >= IntegrityLevel.HIGH


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


class _GUI_THREAD_INFO(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("active", wintypes.HWND),
        ("focus", wintypes.HWND),
        ("capture", wintypes.HWND),
        ("menu_owner", wintypes.HWND),
        ("move_size", wintypes.HWND),
        ("caret", wintypes.HWND),
        ("caret_rect", wintypes.RECT),
    ]


@lru_cache(maxsize=1)
def _configure_apis() -> tuple[Any, Any, Any]:
    user32 = load_library("user32")
    kernel32 = load_library("kernel32")
    advapi32 = load_library("advapi32")

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUI_THREAD_INFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    return user32, kernel32, advapi32


def _as_hwnd(hwnd: int) -> wintypes.HWND:
    return wintypes.HWND(hwnd)


def get_foreground_hwnd() -> int:
    require_windows()
    user32, _, _ = _configure_apis()
    return int(user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    require_windows()
    user32, _, _ = _configure_apis()
    return bool(user32.IsWindow(_as_hwnd(hwnd)))


def get_window_title(hwnd: int) -> str:
    require_windows()
    user32, _, _ = _configure_apis()
    native_hwnd = _as_hwnd(hwnd)
    length = max(0, int(user32.GetWindowTextLengthW(native_hwnd)))
    buffer = ctypes.create_unicode_buffer(length + 1)
    copied = int(user32.GetWindowTextW(native_hwnd, buffer, len(buffer)))
    return buffer.value[:copied]


def get_window_process_id(hwnd: int) -> int:
    require_windows()
    user32, _, _ = _configure_apis()
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(_as_hwnd(hwnd), ctypes.byref(process_id))
    return int(process_id.value)


def _open_process(process_id: int) -> int:
    _, kernel32, _ = _configure_apis()
    return int(kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id) or 0)


def get_process_path(process_id: int) -> str:
    """Return the executable path, or an empty string if access is denied."""

    require_windows()
    if process_id <= 0:
        return ""
    _, kernel32, _ = _configure_apis()
    process = _open_process(process_id)
    if not process:
        return ""
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            wintypes.HANDLE(process), 0, buffer, ctypes.byref(capacity)
        ):
            return ""
        return buffer.value[: capacity.value]
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(process))


def get_process_name(process_id: int) -> str:
    return Path(get_process_path(process_id)).name


def get_process_integrity(process_id: int) -> IntegrityLevel:
    """Read a process mandatory integrity label without requiring elevation."""

    require_windows()
    if process_id <= 0:
        return IntegrityLevel.UNKNOWN
    _, kernel32, advapi32 = _configure_apis()
    process = _open_process(process_id)
    if not process:
        return IntegrityLevel.UNKNOWN
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(
            wintypes.HANDLE(process), TOKEN_QUERY, ctypes.byref(token)
        ):
            return IntegrityLevel.UNKNOWN
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            return IntegrityLevel.UNKNOWN
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_INTEGRITY_LEVEL,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            return IntegrityLevel.UNKNOWN
        label = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
        count_pointer = advapi32.GetSidSubAuthorityCount(label.Label.Sid)
        if not count_pointer:
            return IntegrityLevel.UNKNOWN
        count = int(count_pointer.contents.value)
        if count == 0:
            return IntegrityLevel.UNKNOWN
        rid_pointer = advapi32.GetSidSubAuthority(label.Label.Sid, count - 1)
        if not rid_pointer:
            return IntegrityLevel.UNKNOWN
        return IntegrityLevel.from_rid(int(rid_pointer.contents.value))
    finally:
        if token.value:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(wintypes.HANDLE(process))


@lru_cache(maxsize=1)
def get_current_integrity() -> IntegrityLevel:
    return get_process_integrity(os.getpid())


def compare_integrity(
    target: IntegrityLevel,
    current: IntegrityLevel | None = None,
) -> IntegrityComparison:
    source = get_current_integrity() if current is None else current
    if source is IntegrityLevel.UNKNOWN or target is IntegrityLevel.UNKNOWN:
        return IntegrityComparison.UNKNOWN
    if target > source:
        return IntegrityComparison.TARGET_HIGHER
    return IntegrityComparison.SAFE


def capture_window(hwnd: int) -> WindowMetadata | None:
    """Capture metadata for a live HWND, guarding against stale handles."""

    if not hwnd or not is_window(hwnd):
        return None
    process_id = get_window_process_id(hwnd)
    if process_id <= 0:
        return None
    process_path = get_process_path(process_id)
    return WindowMetadata(
        hwnd=hwnd,
        title=get_window_title(hwnd),
        process_id=process_id,
        process_name=Path(process_path).name,
        integrity_level=get_process_integrity(process_id),
        process_path=process_path or None,
    )


def capture_foreground() -> WindowMetadata | None:
    hwnd = get_foreground_hwnd()
    return capture_window(hwnd) if hwnd else None


def focused_control_type(target: WindowMetadata) -> str | None:
    if os.name != "nt" or get_foreground_hwnd() != target.hwnd:
        return None
    user32, _, _ = _configure_apis()
    process = wintypes.DWORD()
    thread = user32.GetWindowThreadProcessId(_as_hwnd(target.hwnd), ctypes.byref(process))
    if not thread or process.value != target.process_id:
        return None
    info = _GUI_THREAD_INFO()
    info.size = ctypes.sizeof(info)
    if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.focus:
        return None
    output = ctypes.create_unicode_buffer(256)
    length = user32.GetClassNameW(info.focus, output, len(output))
    return output.value if length and get_foreground_hwnd() == target.hwnd else None


def request_foreground(hwnd: int) -> bool:
    """Ask Windows to activate a live window; Windows may lawfully refuse."""

    if not is_window(hwnd):
        return False
    user32, _, _ = _configure_apis()
    return bool(user32.SetForegroundWindow(_as_hwnd(hwnd)))
