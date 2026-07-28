"""Interactive Win32 smoke tests.

Run only in a disposable interactive desktop session:

    $env:VOXTYPE_RUN_WINDOWS_SMOKE = "1"
    pytest tests/windows/test_windows_native_smoke.py -v
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

from voxtype.ui.overlay import DictationOverlay
from voxtype.windows.clipboard import ClipboardManager
from voxtype.windows.foreground import (
    capture_foreground,
    capture_window,
    get_process_name,
    get_window_process_id,
    request_foreground,
)
from voxtype.windows.injection import InjectionStatus, TextInjectionService
from voxtype.windows.send_input import (
    KEYEVENTF_KEYUP,
    VK_CONTROL,
    KeyStroke,
    NativeSendInputBackend,
)
from voxtype.windows.target_window import TargetWindowManager

pytestmark = [
    pytest.mark.windows,
    pytest.mark.interactive,
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("VOXTYPE_RUN_WINDOWS_SMOKE") != "1",
        reason="requires an explicitly enabled interactive Windows desktop",
    ),
]

WM_CLOSE = 0x0010


def _visible_windows() -> list[int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    found: list[int] = []

    @callback_type
    def collect(hwnd: int, lparam: int) -> bool:
        del lparam
        if user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def _find_notepad_window(filename: str, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for hwnd in _visible_windows():
            process_name = get_process_name(get_window_process_id(hwnd)).casefold()
            metadata = capture_window(hwnd)
            if (
                process_name == "notepad.exe"
                and metadata is not None
                and filename.casefold() in metadata.title.casefold()
            ):
                return hwnd
        time.sleep(0.05)
    return 0


def _wait_foreground(hwnd: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = capture_foreground()
        if current is not None and current.hwnd == hwnd:
            return True
        request_foreground(hwnd)
        time.sleep(0.05)
    return False


def _send_ctrl_key(virtual_key: int) -> None:
    backend = NativeSendInputBackend()
    events = (
        KeyStroke(VK_CONTROL, 0, 0),
        KeyStroke(virtual_key, 0, 0),
        KeyStroke(virtual_key, 0, KEYEVENTF_KEYUP),
        KeyStroke(VK_CONTROL, 0, KEYEVENTF_KEYUP),
    )
    assert backend.send_keyboard(events) == len(events)


def test_native_clipboard_unicode_roundtrip_and_restore() -> None:
    clipboard = ClipboardManager()
    snapshot = clipboard.snapshot()
    if not snapshot.complete:
        pytest.skip("current clipboard contains a format that cannot be preserved")
    with clipboard.temporary_text("VoxType: Кириллица + English + 😀") as lease:
        assert clipboard.get_text() == "VoxType: Кириллица + English + 😀"
    assert lease.restored is True


def test_notepad_receives_real_unicode_and_clipboard_is_restored(tmp_path: Path) -> None:
    test_file = tmp_path / "voxtype_native_smoke_unique.txt"
    test_file.write_text("", encoding="utf-8")
    clipboard = ClipboardManager()
    original_clipboard = clipboard.snapshot()
    if not original_clipboard.complete:
        pytest.skip("current clipboard contains a format that cannot be preserved")

    subprocess.Popen(["notepad.exe", str(test_file)])
    hwnd = _find_notepad_window(test_file.name)
    if not hwnd or not _wait_foreground(hwnd):
        pytest.skip("Notepad did not expose an activatable interactive window")

    targets = TargetWindowManager()
    assert targets.start_session().can_inject
    service = TextInjectionService(targets, clipboard=clipboard, paste_delay=0.35)
    expected = "VoxType smoke: English, Кириллица, 😀.\r\nSecond line"
    result = service.submit(expected)
    assert result.status is InjectionStatus.INSERTED
    assert result.clipboard_restored is True

    assert _wait_foreground(hwnd)
    _send_ctrl_key(ord("S"))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        saved = test_file.read_text(encoding="utf-8-sig")
        if "VoxType smoke" in saved:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Notepad did not persist the injected text")
    assert saved.replace("\r\n", "\n") == expected.replace("\r\n", "\n")

    # The test tab is saved, so Ctrl+W closes it without a destructive prompt.
    _send_ctrl_key(ord("W"))
    service.close()


def test_overlay_does_not_take_focus_from_real_notepad(tmp_path: Path) -> None:
    test_file = tmp_path / "voxtype_overlay_focus.txt"
    test_file.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(test_file)])
    hwnd = _find_notepad_window(test_file.name)
    if not hwnd or not _wait_foreground(hwnd):
        pytest.skip("Notepad did not expose an activatable interactive window")

    application = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "overlay.ini"), QSettings.Format.IniFormat)
    overlay = DictationOverlay(settings=settings)
    overlay.show_without_activating()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    foreground = capture_foreground()
    assert foreground is not None
    assert foreground.hwnd != int(overlay.winId())
    assert not overlay.hasFocus()
    overlay.close()
    assert _wait_foreground(hwnd)
    _send_ctrl_key(ord("W"))


def test_locked_target_queues_during_real_window_change_then_returns_fifo(
    tmp_path: Path,
) -> None:
    first_file = tmp_path / "voxtype_target_first.txt"
    first_file.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(first_file)])
    first_hwnd = _find_notepad_window(first_file.name)
    if not first_hwnd or not _wait_foreground(first_hwnd):
        pytest.skip("Notepad did not expose an activatable interactive window")

    targets = TargetWindowManager()
    assert targets.start_session().can_inject
    service = TextInjectionService(targets, paste_delay=0.35)
    application = QApplication.instance() or QApplication([])
    other_window = QWidget()
    other_window.setWindowTitle("VoxType target-change smoke")
    other_window.show()
    application.processEvents()
    assert _wait_foreground(int(other_window.winId()))
    queued = service.submit("locked-target-only")
    assert queued.status is InjectionStatus.QUEUED
    assert first_file.read_text(encoding="utf-8-sig") == ""

    assert _wait_foreground(first_hwnd)
    inserted = service.flush()
    assert inserted.status is InjectionStatus.INSERTED
    _send_ctrl_key(ord("S"))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if first_file.read_text(encoding="utf-8-sig") == "locked-target-only":
            break
        time.sleep(0.05)
    assert first_file.read_text(encoding="utf-8-sig") == "locked-target-only"
    service.close()
    _send_ctrl_key(ord("W"))
    other_window.close()
