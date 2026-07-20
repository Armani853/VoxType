"""Runtime hardware and PortAudio capability discovery for diagnostics."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess

import psutil
import sounddevice as sd

from voxtype.core.models import HardwareInfo

_RATES = (8_000, 16_000, 22_050, 32_000, 44_100, 48_000, 96_000)


def _powershell_json(script: str) -> object:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        return []
    try:
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
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _audio() -> tuple[tuple[str, ...], tuple[int, ...]]:
    try:
        devices = sd.query_devices()
        default = int(sd.default.device[0])
    except (sd.PortAudioError, TypeError, ValueError):
        return (), ()
    names = tuple(
        f"{index}: {item['name']} ({sd.query_hostapis(item['hostapi'])['name']})"
        for index, item in enumerate(devices)
        if int(item["max_input_channels"]) > 0
    )
    rates: list[int] = []
    for rate in _RATES:
        try:
            sd.check_input_settings(device=default, channels=1, samplerate=rate, dtype="float32")
        except sd.PortAudioError:
            continue
        rates.append(rate)
    return names, tuple(rates)


def _vulkan() -> bool:
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


def probe_hardware() -> HardwareInfo:
    memory = psutil.virtual_memory()
    processor = _powershell_json(
        "Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name "
        "| ConvertTo-Json -Compress"
    )
    raw_gpus = _powershell_json(
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name "
        "| ConvertTo-Json -Compress"
    )
    gpus: tuple[str, ...]
    if isinstance(raw_gpus, str):
        gpus = (raw_gpus,)
    elif isinstance(raw_gpus, list):
        gpus = tuple(str(item) for item in raw_gpus)
    else:
        gpus = ()
    inputs, rates = _audio()
    try:
        import ctranslate2

        cuda = bool(ctranslate2.get_cuda_device_count())
    except (ImportError, RuntimeError):
        cuda = False
    return HardwareInfo(
        os_name=platform.platform(),
        cpu_name=processor if isinstance(processor, str) else platform.processor(),
        physical_cores=psutil.cpu_count(logical=False) or 1,
        logical_cores=psutil.cpu_count(logical=True) or 1,
        total_ram_bytes=memory.total,
        available_ram_bytes=memory.available,
        gpu_names=gpus,
        cuda_available=cuda,
        vulkan_available=_vulkan(),
        input_devices=inputs,
        supported_sample_rates=rates,
    )
