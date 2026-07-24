"""Best-effort multi-format clipboard preservation for short paste leases."""

from __future__ import annotations

import ctypes
import secrets
import struct
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from ._win32 import Win32CallError, clear_last_error, last_error, load_library

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ACCESS_DENIED = 5
CF_PRIVATEFIRST = 0x0200
CF_PRIVATELAST = 0x02FF
CF_GDIOBJFIRST = 0x0300
CF_GDIOBJLAST = 0x03FF

# These standard formats contain GDI handles, owner-rendered data, or structures
# with embedded handles; copying their bytes as HGLOBAL would create invalid data.
_NON_HGLOBAL_FORMATS = frozenset({2, 3, 9, 14, 0x0080, 0x0082, 0x0083, 0x008E})

_HISTORY_FORMAT = "CanIncludeInClipboardHistory"
_CLOUD_FORMAT = "CanUploadToCloudClipboard"
_MONITOR_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"
_LEASE_FORMAT = "VoxTypeClipboardLease"


class ClipboardError(RuntimeError):
    """Base class for safe clipboard-operation failures."""


class ClipboardBusyError(ClipboardError):
    """Another application kept the clipboard open past the retry deadline."""


class ClipboardChangedError(ClipboardError):
    """The user changed the clipboard during a protected operation."""


@dataclass(frozen=True, slots=True)
class ClipboardItem:
    format_id: int
    data: bytes
    format_name: str | None = None


@dataclass(frozen=True, slots=True)
class ClipboardSnapshot:
    items: tuple[ClipboardItem, ...]
    format_ids: tuple[int, ...]
    skipped_formats: tuple[int, ...]
    sequence_number: int

    @property
    def complete(self) -> bool:
        return not self.skipped_formats

    @property
    def was_empty(self) -> bool:
        return not self.format_ids


@dataclass(frozen=True, slots=True)
class ClipboardWriteResult:
    sequence_number: int
    written_formats: tuple[int, ...]
    failed_formats: tuple[int, ...]

    @property
    def complete(self) -> bool:
        return not self.failed_formats


class ClipboardBackend(Protocol):
    def sequence_number(self) -> int: ...

    def snapshot(self, *, max_total_bytes: int, max_format_bytes: int) -> ClipboardSnapshot: ...

    def replace(
        self,
        items: Sequence[ClipboardItem],
        *,
        expected_sequence_number: int | None = None,
        expected_marker: ClipboardItem | None = None,
    ) -> ClipboardWriteResult | None: ...

    def register_format(self, name: str) -> int: ...


@lru_cache(maxsize=1)
def _configure_apis() -> tuple[Any, Any]:
    user32 = load_library("user32")
    kernel32 = load_library("kernel32")

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.GetClipboardFormatNameW.argtypes = [
        wintypes.UINT,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetClipboardFormatNameW.restype = ctypes.c_int
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return user32, kernel32


class Win32ClipboardBackend:
    """Raw Win32 backend that copies every available HGLOBAL-backed format."""

    def __init__(self, *, retries: int = 10, retry_delay: float = 0.015) -> None:
        if retries < 1:
            raise ValueError("retries must be at least 1")
        if retry_delay < 0:
            raise ValueError("retry_delay cannot be negative")
        self._retries = retries
        self._retry_delay = retry_delay

    @contextmanager
    def _opened(self) -> Iterator[None]:
        user32, _ = _configure_apis()
        for attempt in range(self._retries):
            clear_last_error()
            if user32.OpenClipboard(None):
                break
            if attempt + 1 < self._retries:
                time.sleep(self._retry_delay)
        else:
            raise ClipboardBusyError(
                f"OpenClipboard failed after {self._retries} attempts "
                f"(Win32 error {last_error() or ERROR_ACCESS_DENIED})"
            )
        try:
            yield
        finally:
            user32.CloseClipboard()

    def sequence_number(self) -> int:
        user32, _ = _configure_apis()
        return int(user32.GetClipboardSequenceNumber())

    @staticmethod
    def _format_name(format_id: int) -> str | None:
        if format_id < 0xC000:
            return None
        user32, _ = _configure_apis()
        buffer = ctypes.create_unicode_buffer(256)
        length = int(user32.GetClipboardFormatNameW(format_id, buffer, len(buffer)))
        return buffer.value[:length] if length else None

    @staticmethod
    def _read_hglobal(format_id: int) -> bytes | None:
        user32, kernel32 = _configure_apis()
        handle = user32.GetClipboardData(format_id)
        if not handle:
            return None
        size = int(kernel32.GlobalSize(handle))
        if size <= 0:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.string_at(pointer, size)
        finally:
            kernel32.GlobalUnlock(handle)

    @staticmethod
    def _can_copy_as_hglobal(format_id: int) -> bool:
        return not (
            format_id in _NON_HGLOBAL_FORMATS
            or CF_PRIVATEFIRST <= format_id <= CF_PRIVATELAST
            or CF_GDIOBJFIRST <= format_id <= CF_GDIOBJLAST
        )

    def snapshot(self, *, max_total_bytes: int, max_format_bytes: int) -> ClipboardSnapshot:
        if max_total_bytes < 1 or max_format_bytes < 1:
            raise ValueError("clipboard snapshot limits must be positive")
        user32, _ = _configure_apis()
        items: list[ClipboardItem] = []
        format_ids: list[int] = []
        skipped: list[int] = []
        total = 0
        sequence = 0
        with self._opened():
            current = 0
            while True:
                clear_last_error()
                current = int(user32.EnumClipboardFormats(current))
                if current == 0:
                    if last_error() != 0:
                        raise ClipboardError(
                            f"EnumClipboardFormats failed (Win32 error {last_error()})"
                        )
                    break
                if current in format_ids:
                    continue
                format_ids.append(current)
                if not self._can_copy_as_hglobal(current):
                    skipped.append(current)
                    continue
                data = self._read_hglobal(current)
                if data is None:
                    skipped.append(current)
                    continue
                if len(data) > max_format_bytes or total + len(data) > max_total_bytes:
                    skipped.append(current)
                    continue
                items.append(ClipboardItem(current, data, self._format_name(current)))
                total += len(data)
            sequence = self.sequence_number()
        return ClipboardSnapshot(
            tuple(items),
            tuple(format_ids),
            tuple(skipped),
            sequence,
        )

    @staticmethod
    def _allocate(data: bytes) -> int:
        _, kernel32 = _configure_apis()
        allocation_size = max(1, len(data))
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, allocation_size)
        if not handle:
            raise Win32CallError("GlobalAlloc")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            raise Win32CallError("GlobalLock")
        try:
            if data:
                ctypes.memmove(pointer, data, len(data))
            else:
                ctypes.memset(pointer, 0, 1)
        finally:
            kernel32.GlobalUnlock(handle)
        return int(handle)

    def replace(
        self,
        items: Sequence[ClipboardItem],
        *,
        expected_sequence_number: int | None = None,
        expected_marker: ClipboardItem | None = None,
    ) -> ClipboardWriteResult | None:
        user32, kernel32 = _configure_apis()
        written: list[int] = []
        failed: list[int] = []
        with self._opened():
            sequence_changed = (
                expected_sequence_number is not None
                and self.sequence_number() != expected_sequence_number
            )
            # Word and some Chromium controls enrich the paste clipboard and
            # increment its sequence while retaining our private lease marker.
            # The marker proves ownership; without it, a sequence change is
            # treated as a user clipboard change and is never overwritten.
            if sequence_changed and expected_marker is None:
                return None
            if expected_marker is not None:
                marker_data = self._read_hglobal(expected_marker.format_id)
                if marker_data != expected_marker.data:
                    return None
            if not user32.EmptyClipboard():
                raise ClipboardError(f"EmptyClipboard failed (Win32 error {last_error()})")
            for item in items:
                handle = 0
                try:
                    handle = self._allocate(item.data)
                    if not user32.SetClipboardData(item.format_id, wintypes.HANDLE(handle)):
                        failed.append(item.format_id)
                        kernel32.GlobalFree(wintypes.HGLOBAL(handle))
                    else:
                        written.append(item.format_id)
                        handle = 0  # The clipboard owns successful allocations.
                except OSError:
                    failed.append(item.format_id)
                    if handle:
                        kernel32.GlobalFree(wintypes.HGLOBAL(handle))
        # Format synthesis can advance the sequence during CloseClipboard.
        return ClipboardWriteResult(self.sequence_number(), tuple(written), tuple(failed))

    def register_format(self, name: str) -> int:
        if not name:
            raise ValueError("clipboard format name cannot be empty")
        user32, _ = _configure_apis()
        format_id = int(user32.RegisterClipboardFormatW(name))
        if not format_id:
            raise ClipboardError(f"RegisterClipboardFormatW failed (Win32 error {last_error()})")
        return format_id


class MemoryClipboardBackend:
    """Deterministic backend useful for tests and non-interactive diagnostics."""

    def __init__(self, items: Sequence[ClipboardItem] = ()) -> None:
        self.items = list(items)
        self._sequence = 1
        self._next_format = 0xC000
        self._registered: dict[str, int] = {}
        self._lock = threading.RLock()

    def sequence_number(self) -> int:
        with self._lock:
            return self._sequence

    def snapshot(self, *, max_total_bytes: int, max_format_bytes: int) -> ClipboardSnapshot:
        with self._lock:
            total = 0
            captured: list[ClipboardItem] = []
            skipped: list[int] = []
            for item in self.items:
                if len(item.data) > max_format_bytes or total + len(item.data) > max_total_bytes:
                    skipped.append(item.format_id)
                else:
                    captured.append(item)
                    total += len(item.data)
            return ClipboardSnapshot(
                tuple(captured),
                tuple(item.format_id for item in self.items),
                tuple(skipped),
                self._sequence,
            )

    def replace(
        self,
        items: Sequence[ClipboardItem],
        *,
        expected_sequence_number: int | None = None,
        expected_marker: ClipboardItem | None = None,
    ) -> ClipboardWriteResult | None:
        with self._lock:
            if expected_sequence_number is not None and expected_sequence_number != self._sequence:
                return None
            if expected_marker is not None and expected_marker not in self.items:
                return None
            self.items = list(items)
            self._sequence += 1
            return ClipboardWriteResult(
                self._sequence,
                tuple(item.format_id for item in items),
                (),
            )

    def register_format(self, name: str) -> int:
        with self._lock:
            if name not in self._registered:
                self._registered[name] = self._next_format
                self._next_format += 1
            return self._registered[name]

    def simulate_external_change(self, items: Sequence[ClipboardItem]) -> None:
        with self._lock:
            self.items = list(items)
            self._sequence += 1


@dataclass(slots=True)
class ClipboardLease:
    snapshot: ClipboardSnapshot
    temporary_sequence_number: int
    restored: bool | None = None


class ClipboardManager:
    """High-level text operations with race-aware restoration."""

    def __init__(
        self,
        backend: ClipboardBackend | None = None,
        *,
        max_total_bytes: int = 64 * 1024 * 1024,
        max_format_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        if max_total_bytes < 1 or max_format_bytes < 1:
            raise ValueError("clipboard snapshot limits must be positive")
        self._backend = backend or Win32ClipboardBackend()
        self._max_total_bytes = max_total_bytes
        self._max_format_bytes = max_format_bytes
        self._lock = threading.RLock()
        self._lease_markers: dict[int, ClipboardItem] = {}

    @property
    def backend(self) -> ClipboardBackend:
        return self._backend

    def snapshot(self) -> ClipboardSnapshot:
        with self._lock:
            return self._backend.snapshot(
                max_total_bytes=self._max_total_bytes,
                max_format_bytes=self._max_format_bytes,
            )

    def _text_items(
        self,
        text: str,
        *,
        private: bool,
        lease_marker: ClipboardItem | None,
    ) -> tuple[ClipboardItem, ...]:
        encoded = text.encode("utf-16-le", errors="strict") + b"\x00\x00"
        items = [ClipboardItem(CF_UNICODETEXT, encoded)]
        if private:
            zero = struct.pack("<I", 0)
            items.extend(
                (
                    ClipboardItem(self._backend.register_format(_HISTORY_FORMAT), zero),
                    ClipboardItem(self._backend.register_format(_CLOUD_FORMAT), zero),
                    ClipboardItem(self._backend.register_format(_MONITOR_FORMAT), b"\x00"),
                )
            )
        if lease_marker is not None:
            items.append(lease_marker)
        return tuple(items)

    def set_text(
        self,
        text: str,
        *,
        expected_sequence_number: int | None = None,
        private: bool = True,
        rollback_snapshot: ClipboardSnapshot | None = None,
    ) -> int:
        lease_marker = None
        if expected_sequence_number is not None:
            lease_marker = ClipboardItem(
                self._backend.register_format(_LEASE_FORMAT), secrets.token_bytes(16)
            )
        with self._lock:
            result = self._backend.replace(
                self._text_items(
                    text,
                    private=private,
                    lease_marker=lease_marker,
                ),
                expected_sequence_number=expected_sequence_number,
            )
        if result is None:
            raise ClipboardChangedError(
                "Clipboard changed before VoxType could place temporary text"
            )
        unicode_written = CF_UNICODETEXT in result.written_formats
        lease_written = lease_marker is None or lease_marker.format_id in result.written_formats
        if not unicode_written or not lease_written:
            restored = False
            if rollback_snapshot is not None:
                rollback = self._backend.replace(
                    rollback_snapshot.items,
                    expected_sequence_number=result.sequence_number,
                    expected_marker=lease_marker if lease_written else None,
                )
                restored = rollback is not None and rollback.complete
            failure = (
                "Unicode clipboard data" if not unicode_written else "clipboard restoration lease"
            )
            suffix = " and the original snapshot was restored" if restored else ""
            raise ClipboardError(f"The {failure} could not be written{suffix}")
        if lease_marker is not None:
            with self._lock:
                self._lease_markers[result.sequence_number] = lease_marker
        return result.sequence_number

    def get_text(self) -> str | None:
        snapshot = self.snapshot()
        for item in snapshot.items:
            if item.format_id != CF_UNICODETEXT:
                continue
            data = item.data
            if len(data) % 2:
                data = data[:-1]
            decoded = data.decode("utf-16-le", errors="replace")
            return decoded.split("\x00", 1)[0]
        return None

    def restore(
        self,
        snapshot: ClipboardSnapshot,
        *,
        expected_sequence_number: int | None = None,
    ) -> bool:
        with self._lock:
            marker = (
                None
                if expected_sequence_number is None
                else self._lease_markers.pop(expected_sequence_number, None)
            )
            result = self._backend.replace(
                snapshot.items,
                expected_sequence_number=expected_sequence_number,
                expected_marker=marker,
            )
        return result is not None and result.complete and snapshot.complete

    def copy_text(self, text: str, *, private: bool = True) -> int:
        """Explicit safe-copy mode: keep confirmed text for a manual paste."""

        return self.set_text(text, private=private)

    @contextmanager
    def temporary_text(self, text: str) -> Iterator[ClipboardLease]:
        """Temporarily replace the clipboard and restore it if no one changed it."""

        snapshot = self.snapshot()
        temporary_sequence = self.set_text(
            text,
            expected_sequence_number=snapshot.sequence_number,
            rollback_snapshot=snapshot,
        )
        lease = ClipboardLease(snapshot, temporary_sequence)
        try:
            yield lease
        finally:
            lease.restored = self.restore(snapshot, expected_sequence_number=temporary_sequence)
