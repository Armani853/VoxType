"""Small deterministic health checks used by diagnostics and packaging smoke tests."""

from __future__ import annotations

import importlib.util
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
    for module in ("numpy", "sounddevice", "PySide6", "faster_whisper", "psutil"):
        checks.append(
            HealthCheck(module, importlib.util.find_spec(module) is not None, "module availability")
        )
    return tuple(checks)

