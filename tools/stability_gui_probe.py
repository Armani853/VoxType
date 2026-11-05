"""Exercise one real Windows GUI lifecycle and inspect its rendered pixels.

The parent stress runner starts this script in a fresh process for each trial.
VOXTYPE_DATA_DIRECTORY must point at a dedicated test profile, never user data.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, cast

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QScrollArea

from voxtype.app import DesktopApplication
from voxtype.asr.base import TranscriptionRequest, TranscriptionResult
from voxtype.audio.models import AudioChunk
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.runtime import build_runtime
from voxtype.settings.repository import SettingsRepository
from voxtype.ui import TrayController
from voxtype.vad.segmenter import StreamingSegmenter


def _inspect_window(shell: DesktopApplication, screenshot: Path | None) -> dict[str, Any]:
    main = shell.main
    central = main.centralWidget()
    if not isinstance(central, QScrollArea):
        raise RuntimeError("main window has no scroll-area central widget")
    viewport = central.viewport()
    canvas = central.widget()
    pixmap = main.screen().grabWindow(int(main.winId()))
    if pixmap.isNull():
        raise RuntimeError("Windows could not capture the main window")
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(screenshot), "PNG")
    picture = pixmap.toImage()
    colors: set[int] = set()
    white_or_black = 0
    samples = 0
    left = max(0, int(picture.width() * 0.05))
    right = max(left + 1, int(picture.width() * 0.95))
    top = max(0, int(picture.height() * 0.08))
    bottom = max(top + 1, int(picture.height() * 0.92))
    for y in range(top, bottom, max(1, (bottom - top) // 32)):
        for x in range(left, right, max(1, (right - left) // 40)):
            color = picture.pixelColor(x, y)
            rgb = color.rgb() & 0xFFFFFF
            colors.add(rgb)
            white_or_black += int(rgb in {0x000000, 0xFFFFFF})
            samples += 1
    return {
        "visible": main.isVisible(),
        "first_content_paint": main._first_content_paint_seen,
        "central_size": [central.width(), central.height()],
        "viewport_size": [viewport.width(), viewport.height()],
        "canvas_size": [canvas.width(), canvas.height()] if canvas else [0, 0],
        "button_visible": main.dictation_button.isVisible(),
        "button_size": [main.dictation_button.width(), main.dictation_button.height()],
        "capture_size": [picture.width(), picture.height()],
        "color_count": len(colors),
        "blank_color_fraction": white_or_black / samples if samples else 1.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--close-delay-ms", type=int, default=1200)
    parser.add_argument("--onboarding", action="store_true")
    parser.add_argument("--window-cycles", type=int, default=0)
    parser.add_argument("--settings-cycles", type=int, default=0)
    parser.add_argument("--exit-via-cross", action="store_true")
    parser.add_argument("--slow-model-load-ms", type=int, default=0)
    parser.add_argument("--start-dictation", action="store_true")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--simulate-audio-disconnect", action="store_true")
    parser.add_argument("--simulate-slow-inference", action="store_true")
    parser.add_argument("--block-gui-ms", type=int, default=0)
    parser.add_argument("--block-tray-show-ms", type=int, default=0)
    arguments = parser.parse_args()
    isolated = os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip()
    if not isolated:
        raise RuntimeError("VOXTYPE_DATA_DIRECTORY is required for the GUI probe")
    data_directory = Path(isolated)
    data_directory.mkdir(parents=True, exist_ok=True)
    settings_repository = SettingsRepository(data_directory / "settings.json")
    settings = settings_repository.load()
    settings.general.onboarding_completed = not arguments.onboarding
    settings.general.start_with_windows = False
    settings.general.minimize_to_tray = not arguments.exit_via_cross
    if (
        arguments.start_dictation
        or arguments.model_path is not None
        or arguments.simulate_audio_disconnect
        or arguments.simulate_slow_inference
    ):
        settings.dictation.auto_insert = False
    settings_repository.save(settings)
    started = time.perf_counter()
    application = QApplication([])
    runtime = build_runtime()
    test_controller = cast(Any, runtime.controller)
    backend = cast(Any, runtime.controller.backend)
    if arguments.model_path is not None:
        if not arguments.model_path.is_dir():
            raise RuntimeError("model path does not exist")
        backend.model = str(arguments.model_path.resolve())
    if arguments.slow_model_load_ms:
        backend.load = lambda: time.sleep(arguments.slow_model_load_ms / 1000)
    if arguments.simulate_audio_disconnect:
        backend.load = lambda: None

        def disconnected_capture(_queue: object) -> None:
            raise OSError("synthetic test audio device disconnect")

        test_controller.capture_factory = disconnected_capture
    inference_started = threading.Event()
    if arguments.simulate_slow_inference:

        class SlowBackend:
            model_id = "synthetic"

            def load(self) -> None:
                pass

            def unload(self) -> None:
                pass

            def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
                inference_started.set()
                time.sleep(2)
                return TranscriptionResult(
                    "",
                    request.language,
                    (),
                    request.duration_seconds,
                    2,
                    self.model_id,
                    "synthetic",
                )

        class SpeechDetector:
            def speech_probability(self, _samples: object, _sample_rate: int) -> float:
                return 1.0

            def reset(self) -> None:
                pass

        class TestCapture:
            def __init__(self, queue: LosslessAudioQueue) -> None:
                self.queue = queue

            def start(self) -> None:
                samples = np.full(16_000, 0.2, dtype=np.float32)
                self.queue.put(AudioChunk(0, samples, 16_000, time.time()))
                self.queue.record_capture(len(samples))

            def stop(self, *, drain_timeout: float = 5.0) -> bool:
                del drain_timeout
                return True

            def stats(self) -> object:
                return self.queue.snapshot()

        test_controller.backend = SlowBackend()
        test_controller.capture_factory = TestCapture
        test_controller.segmenter_factory = lambda: StreamingSegmenter(SpeechDetector())
    if arguments.block_tray_show_ms:
        original_tray_show = TrayController.show

        def delayed_tray_show(tray: TrayController) -> None:
            time.sleep(arguments.block_tray_show_ms / 1000)
            original_tray_show(tray)

        cast(Any, TrayController).show = delayed_tray_show
    shell = DesktopApplication(application, runtime)
    observed: dict[str, Any] = {}

    def inspect() -> None:
        nonlocal observed
        observed = _inspect_window(shell, arguments.screenshot)
        observed["controller_state"] = shell.controller.state.name
        observed["inference_started"] = inference_started.is_set()
        observed["window_cycles"] = arguments.window_cycles
        observed["settings_cycles"] = arguments.settings_cycles
        observed["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        QTimer.singleShot(50, shell.main.close if arguments.exit_via_cross else shell.quit)

    def cycle_settings(index: int) -> None:
        if index >= arguments.settings_cycles:
            QTimer.singleShot(max(200, arguments.close_delay_ms), inspect)
            return
        shell.show_settings()

        def close_settings() -> None:
            shell.settings_window.close()
            QTimer.singleShot(20, lambda: cycle_settings(index + 1))

        QTimer.singleShot(30, close_settings)

    def cycle_window(index: int) -> None:
        if index >= arguments.window_cycles:
            cycle_settings(0)
            return
        shell.main.close()
        if not shell.main.isHidden():
            raise RuntimeError("close button did not hide window to tray")
        shell._show_main()
        QTimer.singleShot(30, lambda: cycle_window(index + 1))

    if arguments.simulate_slow_inference:
        QTimer.singleShot(200, shell.controller.start_dictation)
        QTimer.singleShot(600, shell.controller.stop_dictation)

        def wait_for_inference(attempt: int = 0) -> None:
            if inference_started.is_set() or attempt >= 100:
                inspect()
            else:
                QTimer.singleShot(50, lambda: wait_for_inference(attempt + 1))

        QTimer.singleShot(650, wait_for_inference)
    elif (
        arguments.slow_model_load_ms
        or arguments.start_dictation
        or arguments.model_path is not None
        or arguments.simulate_audio_disconnect
    ):
        QTimer.singleShot(200, shell.controller.start_dictation)
    if not arguments.simulate_slow_inference:
        if arguments.block_gui_ms:
            QTimer.singleShot(250, lambda: time.sleep(arguments.block_gui_ms / 1000))
        QTimer.singleShot(
            250
            if arguments.slow_model_load_ms
            or arguments.start_dictation
            or arguments.model_path is not None
            or arguments.simulate_audio_disconnect
            else 200,
            lambda: cycle_window(0),
        )
    exit_code = application.exec()
    observed["exit_code"] = exit_code
    observed["closed"] = shell._closed
    observed["live_voxtype_threads"] = [
        thread.name for thread in threading.enumerate() if thread.name.startswith("voxtype-")
    ]
    observed["shutdown_elapsed_ms"] = (time.perf_counter() - started) * 1000.0
    print(json.dumps(observed, ensure_ascii=False, sort_keys=True), flush=True)
    return int(exit_code != 0 or not shell._closed)


if __name__ == "__main__":
    raise SystemExit(main())
