"""Profile first and repeated Settings opening in an isolated native Qt process."""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["VOXTYPE_DATA_DIRECTORY"] = str(args.data.resolve())
    os.environ["VOXTYPE_CACHE_DIRECTORY"] = str((args.data.parent / "cache").resolve())

    from PySide6.QtWidgets import QApplication

    from voxtype.app import DesktopApplication
    from voxtype.runtime import build_runtime

    application = QApplication([])
    shell = DesktopApplication(application, build_runtime())
    application.processEvents()
    measurements = []
    profiler = cProfile.Profile()
    for iteration in range(3):
        started = time.perf_counter()
        if iteration == 0:
            profiler.enable()
        shell.show_settings()
        application.processEvents()
        if iteration == 0:
            profiler.disable()
        measurements.append(round((time.perf_counter() - started) * 1000, 2))
        shell.settings_window.hide()
        application.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    profile_path = args.output.with_suffix(".prof")
    profiler.dump_stats(str(profile_path))
    args.output.write_text(
        json.dumps({"settings_open_ms": measurements, "profile": str(profile_path)}, indent=2),
        encoding="utf-8",
    )
    pstats.Stats(profiler).sort_stats("cumtime").print_stats(25)
    shell.quit()
    application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
