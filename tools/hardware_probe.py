#!/usr/bin/env python3
"""Emit the hardware/audio capabilities VoxType uses for profile selection."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import psutil
import sounddevice as sd

from voxtype.core.models import HardwareInfo

_COMMON_SAMPLE_RATES = (8_000, 16_000, 22_050, 32_000, 44_100, 48_000, 96_000)


def _powershell_json(script: str) -> object:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        return []
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _gpu_names() -> tuple[str, ...]:
    raw = _powershell_json(
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name "
        "| ConvertTo-Json -Compress"
    )
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(name) for name in raw)
    return ()


def _cpu_name() -> str:
    raw = _powershell_json(
        "Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name "
        "| ConvertTo-Json -Compress"
    )
    return raw.strip() if isinstance(raw, str) else platform.processor() or "Unknown CPU"


def _audio_capabilities(device: int | None) -> tuple[tuple[str, ...], tuple[int, ...]]:
    devices = sd.query_devices()
    inputs = tuple(
        f"{index}: {entry['name']} ({sd.query_hostapis(entry['hostapi'])['name']})"
        for index, entry in enumerate(devices)
        if int(entry["max_input_channels"]) > 0
    )
    selected = device
    if selected is None:
        default = sd.default.device
        try:
            selected = int(default[0])
        except (TypeError, IndexError):
            selected = int(default)
    supported: list[int] = []
    for rate in _COMMON_SAMPLE_RATES:
        try:
            sd.check_input_settings(device=selected, channels=1, samplerate=rate, dtype="float32")
        except sd.PortAudioError:
            continue
        supported.append(rate)
    return inputs, tuple(supported)


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return bool(ctranslate2.get_cuda_device_count())
    except (ImportError, RuntimeError):
        return False


def _vulkan_available() -> bool:
    executable = shutil.which("vulkaninfo.exe") or shutil.which("vulkaninfo")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = result.stdout + result.stderr
    return result.returncode == 0 or "deviceName" in output or "GPU id" in output


def probe(device: int | None = None) -> HardwareInfo:
    memory = psutil.virtual_memory()
    inputs, rates = _audio_capabilities(device)
    return HardwareInfo(
        os_name=platform.platform(),
        cpu_name=_cpu_name(),
        physical_cores=psutil.cpu_count(logical=False) or 1,
        logical_cores=psutil.cpu_count(logical=True) or 1,
        total_ram_bytes=memory.total,
        available_ram_bytes=memory.available,
        gpu_names=_gpu_names(),
        cuda_available=_cuda_available(),
        vulkan_available=_vulkan_available(),
        input_devices=inputs,
        supported_sample_rates=rates,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, default=None, help="PortAudio input device index")
    parser.add_argument("--output", type=Path, default=None, help="Optional UTF-8 JSON output")
    arguments = parser.parse_args()
    payload = json.dumps(asdict(probe(arguments.device)), ensure_ascii=False, indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
