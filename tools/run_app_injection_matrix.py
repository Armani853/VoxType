"""Interactive insertion acceptance matrix for installed Windows editors.

The tool opens isolated temporary documents/profiles where possible, injects a
known Unicode phrase through the production TextInjectionService, verifies the
actual field or saved file, restores the clipboard, and closes only windows it
created.  Enable it explicitly from an interactive desktop:

    python tools/run_app_injection_matrix.py
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import tempfile
import time
import winreg
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

from voxtype.windows.clipboard import ClipboardManager, ClipboardSnapshot
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

WM_CLOSE = 0x0010
VK_BACK = 0x08
VK_ESCAPE = 0x1B
VK_MENU = 0x12
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def _app_path(executable: str) -> Path | None:
    key_name = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value = winreg.QueryValue(key, None)
        except OSError:
            continue
        path = Path(str(value))
        if path.is_file():
            return path
    return None


def _visible_windows() -> list[int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    @callback_type  # type: ignore[untyped-decorator]
    def collect(hwnd: int, lparam: int) -> bool:
        del lparam
        if user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def _find_window(
    process_names: set[str],
    *,
    title_contains: str = "",
    timeout_s: float = 20.0,
) -> int:
    deadline = time.monotonic() + timeout_s
    normalized_processes = {name.casefold() for name in process_names}
    normalized_title = title_contains.casefold()
    while time.monotonic() < deadline:
        for hwnd in _visible_windows():
            metadata = capture_window(hwnd)
            if metadata is None:
                continue
            process_name = get_process_name(get_window_process_id(hwnd)).casefold()
            if process_name not in normalized_processes:
                continue
            if normalized_title and normalized_title not in metadata.title.casefold():
                continue
            return hwnd
        time.sleep(0.05)
    return 0


def _wait_foreground(hwnd: int, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current = capture_foreground()
        if current is not None and current.hwnd == hwnd:
            return True
        # The matrix is an explicitly interactive test. A synthetic Alt
        # transition grants this foreground process the same activation right
        # as a user's Alt+Tab before SetForegroundWindow.
        _send_key(VK_MENU)
        request_foreground(hwnd)
        time.sleep(0.05)
    return False


def _send_key(virtual_key: int, *, control: bool = False) -> None:
    backend = NativeSendInputBackend()
    events: list[KeyStroke] = []
    if control:
        events.append(KeyStroke(VK_CONTROL, 0, 0))
    events.extend(
        (
            KeyStroke(virtual_key, 0, 0),
            KeyStroke(virtual_key, 0, KEYEVENTF_KEYUP),
        )
    )
    if control:
        events.append(KeyStroke(VK_CONTROL, 0, KEYEVENTF_KEYUP))
    if backend.send_keyboard(tuple(events)) != len(events):
        raise RuntimeError("SendInput did not accept the full verification chord")


def _post_close(hwnd: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)


def _click_client(hwnd: int, *, x_ratio: float = 0.6, y_ratio: float = 0.45) -> None:
    """Place the caret in an isolated test editor without keeping mouse state."""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rectangle = wintypes.RECT()
    origin = wintypes.POINT()
    previous = wintypes.POINT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rectangle)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    user32.GetCursorPos(ctypes.byref(previous))
    x = origin.x + int((rectangle.right - rectangle.left) * x_ratio)
    y = origin.y + int((rectangle.bottom - rectangle.top) * y_ratio)
    if not user32.SetCursorPos(x, y):
        raise ctypes.WinError(ctypes.get_last_error())
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.1)
    user32.SetCursorPos(previous.x, previous.y)


def _restore_clipboard(manager: ClipboardManager, snapshot: ClipboardSnapshot) -> None:
    if not manager.restore(snapshot):
        raise RuntimeError("Could not restore the clipboard after matrix verification")


def _inject(hwnd: int, expected: str) -> TextInjectionService:
    if not _wait_foreground(hwnd):
        raise RuntimeError("Could not activate the target window")
    targets = TargetWindowManager()
    decision = targets.start_session()
    if not decision.can_inject:
        raise RuntimeError(f"Target rejected: {decision.status.value}")
    service = TextInjectionService(targets, paste_delay=0.75)
    result = service.submit(expected)
    for _ in range(3):
        if result.status not in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
            break
        if not _wait_foreground(hwnd):
            break
        result = service.flush()
    if result.status is not InjectionStatus.INSERTED or result.clipboard_restored is False:
        service.close()
        raise RuntimeError(f"Injection failed: {result.status.value}")
    return service


def _verify_selected_text(hwnd: int, expected: str) -> None:
    clipboard = ClipboardManager()
    snapshot = clipboard.snapshot()
    if not snapshot.complete:
        raise RuntimeError("Clipboard contains a format that cannot be restored safely")
    try:
        if not _wait_foreground(hwnd):
            raise RuntimeError("Target lost focus before verification")
        _send_key(ord("A"), control=True)
        _send_key(ord("C"), control=True)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            actual = clipboard.get_text()
            if actual is not None and actual.strip("\r\n\x07") == expected:
                return
            time.sleep(0.05)
        raise AssertionError(f"The real target field returned {actual!r}")
    finally:
        _restore_clipboard(clipboard, snapshot)


def _terminate_created(process: subprocess.Popen[Any], hwnd: int) -> None:
    _post_close(hwnd)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and user32.IsWindow(wintypes.HWND(hwnd)):
        time.sleep(0.05)
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
    # Electron/Chromium workers can keep cache journals open briefly after the
    # top-level HWND disappears.
    time.sleep(0.5)


def _run_vscode(root: Path, expected: str) -> str:
    executable = _app_path("Code.exe")
    if executable is None:
        return "not-installed"
    document = root / "voxtype-vscode-smoke.txt"
    document.write_text("", encoding="utf-8")
    user_data = root / "vscode-profile"
    settings_directory = user_data / "User"
    settings_directory.mkdir(parents=True)
    (settings_directory / "settings.json").write_text(
        json.dumps(
            {
                "workbench.startupEditor": "none",
                "security.workspace.trust.enabled": False,
                "window.commandCenter": False,
            }
        ),
        encoding="utf-8",
    )
    launcher = executable.parent / "bin" / "code.cmd"
    process = subprocess.Popen(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            str(launcher),
            f"--user-data-dir={user_data}",
            f"--extensions-dir={root / 'vscode-extensions'}",
            "--new-window",
            "--disable-extensions",
            "--skip-welcome",
            str(document),
        ]
    )
    hwnd = _find_window({"Code.exe"}, title_contains=document.name)
    if not hwnd:
        raise RuntimeError("VS Code window was not found")
    try:
        # The title appears before Monaco has finished mounting its focused
        # editor. Let that asynchronous startup settle before SendInput.
        assert _wait_foreground(hwnd)
        time.sleep(1.25)
        # Current VS Code opens an Agent panel beside the editor even in a
        # fresh profile; the left third remains the actual Monaco text area.
        _click_client(hwnd, x_ratio=0.3)
        service = _inject(hwnd, expected)
        service.close()
        assert _wait_foreground(hwnd)
        _send_key(ord("S"), control=True)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and document.read_text(encoding="utf-8") != expected:
            time.sleep(0.05)
        actual = document.read_text(encoding="utf-8")
        if actual != expected:
            raise AssertionError(f"VS Code saved unexpected text: {actual!r}")
        return "passed"
    finally:
        _terminate_created(process, hwnd)


def _run_chrome(root: Path, expected: str) -> str:
    executable = _app_path("chrome.exe")
    if executable is None:
        return "not-installed"
    page = root / "voxtype-browser-smoke.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'><title>VoxType Browser Smoke</title>"
        "<textarea autofocus oninput=\"document.title='VoxType Browser Smoke | '+this.value\" "
        "style='width:90vw;height:70vh'></textarea>"
        "<script>onload=()=>document.querySelector('textarea').focus()</script>",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            str(executable),
            f"--user-data-dir={root / 'chrome-profile'}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            page.as_uri(),
        ]
    )
    hwnd = _find_window({"chrome.exe"}, title_contains="VoxType Browser Smoke")
    if not hwnd:
        process.terminate()
        raise RuntimeError("Chrome test window was not found")
    try:
        service = _inject(hwnd, expected)
        service.close()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            metadata = capture_window(hwnd)
            if metadata is not None and expected in metadata.title:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Chrome textarea did not expose the injected text")
        return "passed"
    finally:
        _terminate_created(process, hwnd)


def _run_word(root: Path, expected: str) -> str:
    executable = _app_path("WINWORD.EXE")
    if executable is None:
        return "not-installed"
    # Word is verified with BMP Cyrillic/English. Supplementary-plane emoji is
    # covered by Notepad and Chrome because Word reorders surrogate SendInput
    # on systems where the existing clipboard cannot be snapshotted safely.
    word_expected = expected.replace(", 😀", ".")
    hwnd_path = root / "word-hwnd.txt"
    stop_path = root / "word-stop.flag"
    document_path = root / "word-result.rtf"

    def ps_quote(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    command = (
        "$ErrorActionPreference='Stop';"
        "$word=New-Object -ComObject Word.Application;"
        "$word.Visible=$true;"
        "$doc=$word.Documents.Add();"
        f"$doc.SaveAs2({ps_quote(document_path)},6);"
        "$doc.Activate();$word.Selection.SetRange(0,0);"
        f"[IO.File]::WriteAllText({ps_quote(hwnd_path)},[string]$word.ActiveWindow.Hwnd);"
        f"while(-not (Test-Path -LiteralPath {ps_quote(stop_path)}))"
        "{Start-Sleep -Milliseconds 100};"
        "try{$doc.Close(0)}catch{};try{$word.Quit()}catch{}"
    )
    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", command]
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not hwnd_path.exists():
        if process.poll() is not None:
            raise RuntimeError("Word COM fixture exited before creating a document")
        time.sleep(0.05)
    hwnd = int(hwnd_path.read_text(encoding="utf-8")) if hwnd_path.exists() else 0
    if not hwnd:
        process.terminate()
        raise RuntimeError("Word COM document window was not found")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not document_path.exists():
        time.sleep(0.05)
    if not document_path.exists():
        process.terminate()
        raise RuntimeError("Word COM fixture did not create its temporary document")
    try:
        assert _wait_foreground(hwnd)
        time.sleep(0.5)
        initial_mtime = document_path.stat().st_mtime_ns if document_path.exists() else 0
        clipboard = ClipboardManager()
        clipboard_before = clipboard.snapshot()
        service = _inject(hwnd, word_expected)
        service.close()
        clipboard_after = clipboard.snapshot()
        if (
            clipboard_after.items != clipboard_before.items
            or clipboard_after.format_ids != clipboard_before.format_ids
            or clipboard_after.skipped_formats != clipboard_before.skipped_formats
        ):
            raise AssertionError("Word strategy did not restore the clipboard contents")
        time.sleep(2.0)
        assert _wait_foreground(hwnd)
        _send_key(ord("S"), control=True)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (
            not document_path.exists() or document_path.stat().st_mtime_ns == initial_mtime
        ):
            time.sleep(0.05)
        if not document_path.exists():
            raise RuntimeError("Word did not save its temporary document")
        rtf = document_path.read_text(encoding="latin-1")
        if "VoxType matrix" not in rtf:
            raise AssertionError("Word did not save the injected ASCII text")
        for character in word_expected:
            if character.isascii():
                continue
            cp1251_escape = ""
            try:
                encoded = character.encode("cp1251")
            except UnicodeEncodeError:
                encoded = b""
            else:
                cp1251_escape = "".join(f"\\'{byte:02x}" for byte in encoded)
            units = character.encode("utf-16-le")
            unicode_escapes = []
            for index in range(0, len(units), 2):
                unit = int.from_bytes(units[index : index + 2], "little")
                signed = unit if unit < 0x8000 else unit - 0x10000
                unicode_escapes.append(f"\\u{signed}")
            if not (cp1251_escape and cp1251_escape in rtf) and not all(
                item in rtf for item in unicode_escapes
            ):
                raise AssertionError(f"Word RTF is missing {character!r}")
        return "passed"
    finally:
        stop_path.touch(exist_ok=True)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.terminate()


def _run_telegram(root: Path, expected: str) -> str:
    del root
    # Telegram Desktop's search edit exposes supplementary-plane emoji through
    # UI Automation as U+FFFC even though the visible glyph is correct. Verify
    # the same BMP Cyrillic/English phrase as Word; emoji remains covered by
    # Notepad and Chrome.
    telegram_expected = expected.replace(", 😀", ".")
    existing = _find_window({"Telegram.exe"}, timeout_s=0.1)
    if not existing:
        try:
            os.startfile(r"shell:AppsFolder\Telegram.TelegramDesktop")
        except OSError:
            return "not-installed"
    hwnd = existing or _find_window({"Telegram.exe"}, timeout_s=20.0)
    if not hwnd:
        return "not-installed"
    if not _wait_foreground(hwnd):
        raise RuntimeError("Telegram could not be activated")
    expected_literal = "'" + telegram_expected.replace("'", "''") + "'"
    focus_script = (
        "Add-Type -AssemblyName UIAutomationClient;"
        f"$root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd});"
        "$c=New-Object Windows.Automation.PropertyCondition("
        "[Windows.Automation.AutomationElement]::ControlTypeProperty,"
        "[Windows.Automation.ControlType]::Edit);"
        "$items=$root.FindAll([Windows.Automation.TreeScope]::Subtree,$c);"
        "$ok=$false;foreach($item in $items){"
        "if($item.Current.IsKeyboardFocusable -and $item.Current.Name -match 'Search|Поиск'){"
        "$item.SetFocus();$ok=$true;break}};if(-not $ok){exit 2}"
    )
    focused = (
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", focus_script],
            check=False,
        ).returncode
        == 0
    )
    if not focused:
        raise RuntimeError("Telegram search field was not exposed through UI Automation")
    time.sleep(0.2)
    service = _inject(hwnd, telegram_expected)
    service.close()
    try:
        verify_script = (
            "Add-Type -AssemblyName UIAutomationClient;"
            f"$root=[Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd});"
            "$c=New-Object Windows.Automation.PropertyCondition("
            "[Windows.Automation.AutomationElement]::ControlTypeProperty,"
            "[Windows.Automation.ControlType]::Edit);"
            "$items=$root.FindAll([Windows.Automation.TreeScope]::Subtree,$c);"
            "$found=$false;foreach($item in $items){try{"
            "$p=$item.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern);"
            "Write-Output ('VALUE='+$p.Current.Value);"
            f"if($p.Current.Value -eq {expected_literal}){{$found=$true;break}}"
            "}catch{}};if(-not $found){exit 3}"
        )
        verification = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", verify_script],
            check=False,
            capture_output=True,
            text=True,
        )
        if verification.returncode != 0:
            raise AssertionError(
                "Telegram search field did not contain the injected text: "
                + verification.stdout.strip()
            )
        return "passed"
    finally:
        assert _wait_foreground(hwnd)
        _send_key(ord("A"), control=True)
        _send_key(VK_BACK)
        _send_key(VK_ESCAPE)


def run_matrix(selected: set[str] | None = None) -> dict[str, str]:
    expected = "VoxType matrix: Кириллица, English, 😀"
    runners: tuple[tuple[str, Callable[[Path, str], str]], ...] = (
        # Electron editor startup is the most focus-sensitive; validate it
        # before Chromium/Office create additional foreground transitions.
        ("vscode", _run_vscode),
        ("word", _run_word),
        ("chrome", _run_chrome),
        ("telegram", _run_telegram),
    )
    results: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="voxtype-app-matrix-") as temporary:
        root = Path(temporary)
        for name, runner in runners:
            if selected is not None and name not in selected:
                continue
            try:
                results[name] = runner(root, expected)
            except Exception as exc:
                results[name] = f"failed:{type(exc).__name__}:{exc}"
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", nargs="*", choices=("word", "chrome", "vscode", "telegram"))
    arguments = parser.parse_args()
    results = run_matrix(set(arguments.apps) if arguments.apps else None)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any(status.startswith("failed:") for status in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
