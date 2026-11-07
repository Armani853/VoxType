"""Profile an isolated installed VoxType process without reading user content.

The tool measures process launch to log milestones and samples process resource
use. It runs only the explicit EXE and profile paths passed by the caller.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import psutil


def _log_milestones(path: Path, launched_at: datetime) -> dict[str, float]:
    if not path.exists():
        return {}
    patterns = {
        "first_content_paint_ms": "first_content_paint",
        "shell_ready_ms": "startup_stage name=shell_ready",
        "event_loop_exit_ms": "startup_stage name=event_loop_exit",
        "shutdown_requested_ms": "current=shutting_down",
    }
    found: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            timestamp = datetime.fromisoformat(line.split(" ", 1)[0])
        except ValueError:
            continue
        elapsed_ms = (timestamp - launched_at).total_seconds() * 1000.0
        for key, marker in patterns.items():
            if marker in line and key not in found:
                found[key] = round(elapsed_ms, 1)
    return found


def profile(
    exe: Path,
    data: Path,
    cache: Path,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, object]:
    exe = exe.resolve(strict=True)
    data = data.resolve()
    cache = cache.resolve()
    data.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    log_path = data / "logs" / "voxtype.log"
    if log_path.exists():
        raise FileExistsError(f"Use a fresh profile, log already exists: {log_path}")
    environment = os.environ.copy()
    environment["VOXTYPE_DATA_DIRECTORY"] = str(data)
    environment["VOXTYPE_CACHE_DIRECTORY"] = str(cache)
    launched_at = datetime.now()
    started = time.perf_counter()
    process = subprocess.Popen([str(exe), "--ui-smoke-test"], env=environment)
    tracked = psutil.Process(process.pid)
    peak_rss = 0
    peak_threads = 0
    peak_handles = 0
    sampled_cpu_seconds = 0.0
    samples = 0
    forced_stop = False
    while process.poll() is None:
        if time.perf_counter() - started > timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
            forced_stop = True
            break
        try:
            peak_rss = max(peak_rss, tracked.memory_info().rss)
            peak_threads = max(peak_threads, tracked.num_threads())
            peak_handles = max(peak_handles, tracked.num_handles())
            cpu_times = tracked.cpu_times()
            sampled_cpu_seconds = max(sampled_cpu_seconds, cpu_times.user + cpu_times.system)
            samples += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        time.sleep(0.02)
    total_ms = round((time.perf_counter() - started) * 1000.0, 1)
    milestones = _log_milestones(log_path, launched_at)
    result: dict[str, object] = {
        "exe": str(exe),
        "pid": process.pid,
        "started_local": launched_at.isoformat(timespec="milliseconds"),
        "exit_code": process.returncode,
        "forced_stop": forced_stop,
        "process_wall_ms": total_ms,
        "peak_rss_bytes": peak_rss,
        "peak_threads": peak_threads,
        "peak_handles": peak_handles,
        "sampled_cpu_seconds": round(sampled_cpu_seconds, 3),
        "resource_samples": samples,
        "milestones_from_launch_ms": milestones,
        "shutdown_ms": round(
            milestones["event_loop_exit_ms"] - milestones["shutdown_requested_ms"], 1
        )
        if "event_loop_exit_ms" in milestones and "shutdown_requested_ms" in milestones
        else None,
    }
    if log_path.exists():
        log = log_path.read_text(encoding="utf-8", errors="replace")
        result["ui_stall_ms"] = [
            float(value) for value in re.findall(r"UI_STALL state=\S+ elapsed_ms=([\d.]+)", log)
        ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    result = profile(args.exe, args.data, args.cache, timeout_seconds=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["exit_code"] == 0 and not result["forced_stop"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
