"""Sample one known build process tree without recording command lines."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import psutil


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    root = psutil.Process(args.pid)
    started = time.perf_counter()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    peak_rss = 0
    peak_compilers = 0
    samples = 0
    with args.output.open("w", encoding="utf-8") as output:
        while time.perf_counter() - started < args.timeout:
            if not root.is_running() or root.status() == psutil.STATUS_ZOMBIE:
                break
            processes = [root, *root.children(recursive=True)]
            rss = 0
            names: Counter[str] = Counter()
            for process in processes:
                try:
                    rss += process.memory_info().rss
                    names[process.name().casefold()] += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            compiler_count = names["cl.exe"] + names["link.exe"]
            peak_compilers = max(peak_compilers, compiler_count)
            peak_rss = max(peak_rss, rss)
            output.write(
                json.dumps(
                    {
                        "elapsed_s": round(time.perf_counter() - started, 3),
                        "rss_bytes": rss,
                        "compiler_count": compiler_count,
                        "process_count": sum(names.values()),
                    }
                )
                + "\n"
            )
            output.flush()
            samples += 1
            time.sleep(args.interval)
    print(
        json.dumps(
            {"samples": samples, "peak_rss_bytes": peak_rss, "peak_compilers": peak_compilers}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
