"""Sample one exact test process during an interactive runtime scenario."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import psutil


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    process = psutil.Process(args.pid)
    started = time.perf_counter()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        while time.perf_counter() - started < args.duration and process.is_running():
            try:
                cpu = process.cpu_times()
                sample = {
                    "elapsed_s": round(time.perf_counter() - started, 3),
                    "rss_bytes": process.memory_info().rss,
                    "cpu_time_s": round(cpu.user + cpu.system, 3),
                    "threads": process.num_threads(),
                    "handles": process.num_handles(),
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
            output.write(json.dumps(sample) + "\n")
            output.flush()
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
