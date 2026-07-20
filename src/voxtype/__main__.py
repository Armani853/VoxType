"""VoxType command-line and desktop entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from platformdirs import user_data_path

from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS, ModelManager
from voxtype.asr.profiles import ASRProfile
from voxtype.constants import APP_AUTHOR, APP_NAME
from voxtype.monitoring.hardware import probe_hardware
from voxtype.monitoring.health import run_health_checks
from voxtype.runtime import build_runtime, install_silero_vad
from voxtype.version import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voxtype", description="Локальная диктовка VoxType")
    parser.add_argument("--version", action="version", version=f"VoxType {__version__}")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--no-ui", action="store_true")
    parser.add_argument("--install-model", choices=("fast", "balanced", "maximum_accuracy"))
    return parser


def _diagnostics() -> dict[str, object]:
    data = user_data_path(APP_NAME, APP_AUTHOR)
    hardware = probe_hardware()
    checks = run_health_checks(data)
    return {
        "version": __version__,
        "hardware": {
            "os": hardware.os_name,
            "cpu": hardware.cpu_name,
            "physical_cores": hardware.physical_cores,
            "logical_cores": hardware.logical_cores,
            "ram_bytes": hardware.total_ram_bytes,
            "gpu": hardware.gpu_names,
            "cuda": hardware.cuda_available,
            "vulkan": hardware.vulkan_available,
            "input_devices": hardware.input_devices,
            "sample_rates": hardware.supported_sample_rates,
        },
        "checks": [asdict(item) for item in checks],
        "ok": all(item.ok for item in checks),
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.install_model:
        profile = ASRProfile(arguments.install_model)
        manager = ModelManager(user_data_path(APP_NAME, APP_AUTHOR) / "models")
        path = manager.install(DEFAULT_MODEL_SPECS[profile])
        vad_path = install_silero_vad(user_data_path(APP_NAME, APP_AUTHOR))
        sys.stdout.write(
            json.dumps({"installed": str(path), "vad": str(vad_path)}, ensure_ascii=False) + "\n"
        )
        return 0
    if arguments.self_test or arguments.diagnostics or arguments.no_ui:
        report = _diagnostics()
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        return 0 if report["ok"] else 1
    from voxtype.app import run_desktop

    return run_desktop(build_runtime(verbose=arguments.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
