"""Run 30 real insertion attempts in Notepad, VS Code and Chrome.

This is an explicitly interactive Windows acceptance tool.  It uses the
production target, clipboard and SendInput services, isolated app profiles and
deterministic Unicode fragments.  Only windows and temporary files created by
the tool are closed or removed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from run_app_injection_matrix import (
    VK_BACK,
    _app_path,
    _click_client,
    _find_window,
    _post_close,
    _send_key,
    _terminate_created,
    _wait_foreground,
)
from voxtype.windows.clipboard import ClipboardManager
from voxtype.windows.foreground import capture_window
from voxtype.windows.injection import InjectionStatus, TextInjectionService
from voxtype.windows.target_window import TargetWindowManager


@dataclass(slots=True)
class ReliabilityResult:
    attempts: int
    success: int
    failures: int
    lost: int
    duplicates: int
    clipboard_restored: bool
    final_text_verified: bool
    latency_p50_ms: float
    latency_p95_ms: float
    status: str = "passed"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _fragments(name: str, attempts: int) -> list[str]:
    return [f"VT-{name}-{index:02d} Кириллица English 😀\n" for index in range(attempts)]


def _submit_many(hwnd: int, name: str, attempts: int) -> tuple[ReliabilityResult, str]:
    if not _wait_foreground(hwnd):
        raise RuntimeError(f"{name}: target could not be activated")
    clipboard = ClipboardManager()
    before = clipboard.snapshot()
    if not before.complete:
        raise RuntimeError(f"{name}: current clipboard cannot be restored safely")
    targets = TargetWindowManager()
    if not targets.start_session().can_inject:
        raise RuntimeError(f"{name}: target was rejected")
    service = TextInjectionService(targets, clipboard=clipboard, paste_delay=0.75)
    fragments = _fragments(name, attempts)
    latencies: list[float] = []
    success = 0
    failures = 0
    try:
        for index, fragment in enumerate(fragments):
            if not _wait_foreground(hwnd):
                failures += 1
                continue
            started = time.perf_counter()
            result = service.submit(fragment, fragment_id=f"qa-{name}-{index}")
            if result.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}:
                result = service.flush()
            latencies.append((time.perf_counter() - started) * 1000.0)
            if result.status is InjectionStatus.INSERTED and result.clipboard_restored is True:
                success += 1
            else:
                failures += 1
    finally:
        service.close()
    after = clipboard.snapshot()
    clipboard_restored = (
        after.items == before.items
        and after.format_ids == before.format_ids
        and after.skipped_formats == before.skipped_formats
    )
    expected = "".join(fragments)
    report = ReliabilityResult(
        attempts=attempts,
        success=success,
        failures=failures,
        lost=0,
        duplicates=0,
        clipboard_restored=clipboard_restored,
        final_text_verified=False,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
    )
    return report, expected


def _run_notepad(root: Path, attempts: int) -> ReliabilityResult:
    document = root / "voxtype-reliability-notepad.txt"
    document.write_text("", encoding="utf-8")
    process = subprocess.Popen(["notepad.exe", str(document)])
    hwnd = _find_window({"notepad.exe"}, title_contains=document.name)
    if not hwnd:
        process.terminate()
        raise RuntimeError("Notepad window was not found")
    try:
        assert _wait_foreground(hwnd)
        _click_client(hwnd, x_ratio=0.45)
        result, expected = _submit_many(hwnd, "notepad", attempts)
        assert _wait_foreground(hwnd)
        _send_key(ord("S"), control=True)
        deadline = time.monotonic() + 5.0
        actual = ""
        while time.monotonic() < deadline:
            actual = document.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            if len(actual) >= len(expected):
                break
            time.sleep(0.05)
        result.final_text_verified = actual == expected
        _apply_integrity_counts(result, actual, expected)
        return result
    finally:
        _post_close(hwnd)
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.terminate()


def _run_vscode(root: Path, attempts: int) -> ReliabilityResult:
    executable = _app_path("Code.exe")
    if executable is None:
        raise RuntimeError("VS Code is not installed")
    document = root / f"voxtype-reliability-vscode-{root.name[-8:]}.txt"
    document.write_text("", encoding="utf-8")
    user_data = root / "vscode-profile"
    settings_directory = user_data / "User"
    settings_directory.mkdir(parents=True)
    (settings_directory / "settings.json").write_text(
        json.dumps(
            {
                # The verifier profile is intentionally disposable. Keep account-
                # backed features disabled so its window never asks the user to
                # authenticate into a profile that will be deleted after the run.
                "chat.disableAIFeatures": True,
                "github.copilot.enable": {"*": False},
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
            "--disable-extension",
            "GitHub.copilot-chat",
            "--disable-extension",
            "vscode.github-authentication",
            "--disable-extension",
            "vscode.github",
            "--skip-welcome",
            str(document),
        ]
    )
    hwnd = _find_window({"Code.exe"}, title_contains=document.name)
    if not hwnd:
        process.terminate()
        raise RuntimeError("VS Code window was not found")
    try:
        assert _wait_foreground(hwnd)
        time.sleep(1.25)
        _click_client(hwnd, x_ratio=0.3)
        time.sleep(0.5)
        warmup, warmup_text = _submit_many(hwnd, "vscode-warmup", 1)
        if warmup.success != 1:
            raise RuntimeError("VS Code warm-up insertion failed")
        assert _wait_foreground(hwnd)
        _send_key(ord("S"), control=True)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if document.read_text(encoding="utf-8").replace("\r\n", "\n") == warmup_text:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("VS Code caret warm-up did not reach the isolated document")
        assert _wait_foreground(hwnd)
        _send_key(ord("A"), control=True)
        _send_key(VK_BACK)
        _send_key(ord("S"), control=True)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and document.read_text(encoding="utf-8"):
            time.sleep(0.05)
        if document.read_text(encoding="utf-8"):
            raise RuntimeError("VS Code isolated document could not be reset after warm-up")
        result, expected = _submit_many(hwnd, "vscode", attempts)
        time.sleep(0.5)
        assert _wait_foreground(hwnd)
        _send_key(ord("S"), control=True)
        deadline = time.monotonic() + 5.0
        actual = ""
        while time.monotonic() < deadline:
            actual = document.read_text(encoding="utf-8").replace("\r\n", "\n")
            if len(actual) >= len(expected):
                break
            time.sleep(0.05)
        result.final_text_verified = actual == expected
        _apply_integrity_counts(result, actual, expected)
        return result
    finally:
        _terminate_created(process, hwnd)


def _run_chrome(root: Path, attempts: int) -> ReliabilityResult:
    executable = _app_path("chrome.exe")
    if executable is None:
        raise RuntimeError("Chrome is not installed")
    token = root.name[-8:]
    title = f"VoxType Reliability {token}"
    page = root / f"voxtype-reliability-browser-{token}.html"
    title_literal = json.dumps(title)
    page.write_text(
        f"<!doctype html><meta charset='utf-8'><title>{title}</title>"
        "<textarea autofocus style='width:90vw;height:70vh'></textarea><script>"
        f"const prefix={title_literal};const box=document.querySelector('textarea');"
        "function hash(s){let h=2166136261;for(let i=0;i<s.length;i++){"
        "h=Math.imul(h^s.charCodeAt(i),16777619)}return(h>>>0).toString(16)}"
        "box.oninput=()=>document.title=`${prefix} | ${box.value.length} | "
        "${hash(box.value)}`;onload=()=>box.focus()</script>",
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
    hwnd = _find_window({"chrome.exe"}, title_contains=title)
    if not hwnd:
        process.terminate()
        raise RuntimeError("Chrome window was not found")
    try:
        assert _wait_foreground(hwnd)
        time.sleep(0.75)
        _click_client(hwnd, x_ratio=0.5)
        time.sleep(0.25)
        warmup, warmup_text = _submit_many(hwnd, "browser-warmup", 1)
        if warmup.success != 1 or not _wait_for_browser_value(hwnd, title, warmup_text):
            raise RuntimeError("Chrome caret warm-up did not reach the isolated textarea")
        assert _wait_foreground(hwnd)
        _send_key(ord("A"), control=True)
        _send_key(VK_BACK)
        if not _wait_for_browser_value(hwnd, title, ""):
            raise RuntimeError("Chrome isolated textarea could not be reset after warm-up")
        result, expected = _submit_many(hwnd, "browser", attempts)
        result.final_text_verified = _wait_for_browser_value(hwnd, title, expected)
        if not result.final_text_verified:
            result.lost = attempts
            result.status = "failed"
        return result
    finally:
        _terminate_created(process, hwnd)


def _apply_integrity_counts(result: ReliabilityResult, actual: str, expected: str) -> None:
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    result.lost = sum(max(0, 1 - actual_lines.count(line)) for line in expected_lines)
    result.duplicates = sum(max(0, actual_lines.count(line) - 1) for line in expected_lines)
    if (
        result.failures
        or result.lost
        or result.duplicates
        or not result.clipboard_restored
        or not result.final_text_verified
    ):
        result.status = "failed"


def _fnv1a_utf16(text: str) -> str:
    value = 2_166_136_261
    encoded = text.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        code_unit = int.from_bytes(encoded[index : index + 2], "little")
        value = ((value ^ code_unit) * 16_777_619) & 0xFFFFFFFF
    return f"{value:x}"


def _wait_for_browser_value(hwnd: int, title: str, expected: str, timeout_s: float = 3.0) -> bool:
    expected_title = f"{title} | {len(expected.encode('utf-16-le')) // 2} | "
    expected_title += _fnv1a_utf16(expected)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        metadata = capture_window(hwnd)
        if metadata is not None and metadata.title == expected_title:
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument(
        "--apps",
        nargs="*",
        choices=("notepad", "vscode", "browser"),
        default=("notepad", "vscode", "browser"),
    )
    arguments = parser.parse_args()
    if arguments.attempts < 1:
        parser.error("--attempts must be positive")
    results: dict[str, ReliabilityResult] = {}
    with tempfile.TemporaryDirectory(prefix="voxtype-insertion-reliability-") as temporary:
        root = Path(temporary)
        for name, runner in (
            ("notepad", _run_notepad),
            ("vscode", _run_vscode),
            ("browser", _run_chrome),
        ):
            if name not in arguments.apps:
                continue
            try:
                results[name] = runner(root, arguments.attempts)
            except Exception as exc:
                results[name] = ReliabilityResult(
                    attempts=arguments.attempts,
                    success=0,
                    failures=arguments.attempts,
                    lost=arguments.attempts,
                    duplicates=0,
                    clipboard_restored=False,
                    final_text_verified=False,
                    latency_p50_ms=0.0,
                    latency_p95_ms=0.0,
                    status=f"failed:{type(exc).__name__}:{exc}",
                )
    print(
        json.dumps({name: asdict(result) for name, result in results.items()}, ensure_ascii=False)
    )
    return int(any(result.status != "passed" for result in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
