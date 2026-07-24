"""Small deterministic health checks used by diagnostics and packaging smoke tests."""

from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str


def run_health_checks(data_directory: Path) -> tuple[HealthCheck, ...]:
    data_directory.mkdir(parents=True, exist_ok=True)
    checks = [
        HealthCheck("data_directory", data_directory.is_dir(), str(data_directory)),
        HealthCheck(
            "disk_space",
            shutil.disk_usage(data_directory).free >= 2 * 1024**3,
            str(shutil.disk_usage(data_directory).free),
        ),
    ]
    for module in (
        "numpy",
        "sounddevice",
        "PySide6",
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "psutil",
    ):
        try:
            imported = importlib.import_module(module)
            detail = str(getattr(imported, "__version__", "imported"))
        except (ImportError, OSError, RuntimeError) as exc:
            checks.append(HealthCheck(module, False, type(exc).__name__))
        else:
            checks.append(HealthCheck(module, True, detail))
    try:
        import ctranslate2

        compute_types = sorted(ctranslate2.get_supported_compute_types("cpu"))
        checks.append(HealthCheck("ctranslate2_cpu", bool(compute_types), ",".join(compute_types)))
    except (ImportError, OSError, RuntimeError) as exc:
        checks.append(HealthCheck("ctranslate2_cpu", False, type(exc).__name__))
    try:
        import sounddevice

        inputs = sum(
            int(item.get("max_input_channels", 0) > 0) for item in sounddevice.query_devices()
        )
        checks.append(HealthCheck("audio_inputs", inputs > 0, str(inputs)))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        checks.append(HealthCheck("audio_inputs", False, type(exc).__name__))
    return tuple(checks)
