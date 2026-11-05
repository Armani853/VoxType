"""Verify that a normal installed EXE exits when X is pressed without tray mode."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from voxtype.settings.repository import SettingsRepository


def _window_for_pid(pid: int) -> int:
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _param: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            matches.append(int(hwnd))
        return True

    callback_handle = callback_type(callback)
    user32.EnumWindows(callback_handle, 0)
    return matches[0] if matches else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve(strict=True)
    profile = arguments.profile.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    repository = SettingsRepository(profile / "settings.json")
    settings = repository.load()
    settings.general.onboarding_completed = True
    settings.general.minimize_to_tray = False
    settings.general.start_with_windows = False
    repository.save(settings)
    environment = os.environ.copy()
    environment["VOXTYPE_DATA_DIRECTORY"] = str(profile)
    environment["VOXTYPE_CACHE_DIRECTORY"] = str(profile / "cache")
    process = subprocess.Popen([str(executable)], env=environment)
    started = time.monotonic()
    hwnd = 0
    while time.monotonic() - started < 12 and process.poll() is None:
        hwnd = _window_for_pid(process.pid)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        print(json.dumps({"pid": process.pid, "error": "visible main window not found"}))
        return 2
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    try:
        exit_code = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        print(json.dumps({"pid": process.pid, "hwnd": hwnd, "error": "failed close timeout"}))
        return 3
    print(
        json.dumps(
            {
                "pid": process.pid,
                "hwnd": hwnd,
                "exit_code": exit_code,
                "elapsed_s": round(time.monotonic() - started, 2),
            }
        )
    )
    return int(exit_code != 0)


if __name__ == "__main__":
    raise SystemExit(main())
