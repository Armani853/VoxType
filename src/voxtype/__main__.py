"""VoxType command-line and desktop entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

import numpy as np

from voxtype.asr.base import TranscriptionRequest
from voxtype.asr.faster_whisper_backend import FasterWhisperBackend
from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS, ModelManager
from voxtype.asr.profiles import ASRProfile, get_profile
from voxtype.monitoring.hardware import probe_hardware
from voxtype.monitoring.health import run_health_checks
from voxtype.runtime import build_runtime, data_directory_path, install_silero_vad
from voxtype.version import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voxtype", description="Локальная диктовка VoxType")
    parser.add_argument("--version", action="version", version=f"VoxType {__version__}")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--no-ui", action="store_true")
    parser.add_argument("--ui-smoke-test", action="store_true")
    parser.add_argument("--inference-smoke-test", action="store_true")
    parser.add_argument("--install-model", choices=("fast", "balanced", "maximum_accuracy"))
    return parser


def _diagnostics() -> dict[str, object]:
    data = data_directory_path()
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
        manager = ModelManager(data_directory_path() / "models")
        path = manager.install(DEFAULT_MODEL_SPECS[profile])
        vad_path = install_silero_vad(data_directory_path())
        sys.stdout.write(
            json.dumps({"installed": str(path), "vad": str(vad_path)}, ensure_ascii=False) + "\n"
        )
        return 0
    if arguments.inference_smoke_test:
        data = data_directory_path()
        vad_path = install_silero_vad(data)
        from voxtype.vad.silero import SileroVAD

        probability = SileroVAD(vad_path, strict=True).speech_probability(
            np.zeros(512, dtype=np.float32), 16_000
        )
        manager = ModelManager(data / "models")
        smoke_config = get_profile(ASRProfile.BALANCED)
        if not manager.is_installed(smoke_config.model_name):
            sys.stderr.write("balanced model is not installed for inference smoke-test\n")
            return 2
        backend = FasterWhisperBackend(manager.path_for(smoke_config.model_name), smoke_config)
        result = backend.transcribe(
            TranscriptionRequest(np.zeros(16_000, dtype=np.float32), 16_000, "ru")
        )
        sys.stdout.write(
            json.dumps(
                {
                    "vad_probability": probability,
                    "model": result.model_id,
                    "audio_seconds": result.audio_duration_seconds,
                }
            )
            + "\n"
        )
        return 0
    if arguments.ui_smoke_test:
        from PySide6.QtCore import QTimer

        from voxtype.app import run_desktop
        from voxtype.windows.single_instance import SingleInstanceGuard

        guard = SingleInstanceGuard()
        if not guard.acquire():
            guard.activate_existing()
            return 0
        try:
            runtime = build_runtime(verbose=arguments.verbose)
            from PySide6.QtWidgets import QApplication

            application = QApplication.instance() or QApplication([])
            QTimer.singleShot(750, application.quit)
            return run_desktop(runtime)
        finally:
            guard.close()
    if arguments.self_test or arguments.diagnostics or arguments.no_ui:
        report = _diagnostics()
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        return 0 if report["ok"] else 1
    from voxtype.app import run_desktop
    from voxtype.windows.single_instance import SingleInstanceGuard

    guard = SingleInstanceGuard()
    if not guard.acquire():
        guard.activate_existing()
        return 0
    try:
        return run_desktop(build_runtime(verbose=arguments.verbose))
    finally:
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
