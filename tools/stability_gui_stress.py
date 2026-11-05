"""Repeat isolated real-GUI source probes and retain machine-readable evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--close-delay-ms", type=int, default=250)
    parser.add_argument("--timeout-s", type=float, default=15)
    parser.add_argument("--executable", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    evidence = root / "build" / "stability-audit"
    evidence.mkdir(parents=True, exist_ok=True)
    profile = evidence / f"profile-{arguments.label}"
    profile.mkdir(parents=True, exist_ok=True)
    results = evidence / f"{arguments.label}.jsonl"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    environment["VOXTYPE_DATA_DIRECTORY"] = str(profile)
    environment["VOXTYPE_CACHE_DIRECTORY"] = str(evidence / f"cache-{arguments.label}")
    packaged = arguments.executable is not None
    log_path = profile / "logs" / "voxtype.log"
    failures = 0
    started = time.perf_counter()
    with results.open("w", encoding="utf-8") as output:
        for index in range(arguments.count):
            command = (
                [str(arguments.executable.resolve()), "--ui-smoke-test"]
                if arguments.executable is not None
                else [
                    sys.executable,
                    str(root / "tools" / "stability_gui_probe.py"),
                    "--close-delay-ms",
                    str(arguments.close_delay_ms),
                ]
            )
            run_started = time.perf_counter()
            log_before = (
                log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            )
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=arguments.timeout_s,
                    check=False,
                )
                result = (
                    {"exit_code": completed.returncode}
                    if packaged
                    else json.loads(completed.stdout.strip().splitlines()[-1])
                )
                result["returncode"] = completed.returncode
                result["stderr"] = completed.stderr[-3000:]
            except (subprocess.TimeoutExpired, ValueError, IndexError) as exc:
                result = {"error": repr(exc)}
            result["index"] = index + 1
            result["wall_ms"] = round((time.perf_counter() - run_started) * 1000, 1)
            if packaged:
                log_after = (
                    log_path.read_text(encoding="utf-8", errors="replace")
                    if log_path.exists()
                    else ""
                )
                segment = (
                    log_after[len(log_before) :] if log_after.startswith(log_before) else log_after
                )
                result["rendered"] = "ui_smoke_render ok=True" in segment
                result["clean_event_loop_exit"] = "startup_stage name=event_loop_exit" in segment
                result["shutdown_failed"] = "shutdown_worker_failed" in segment
                result["success"] = (
                    result.get("returncode") == 0
                    and result["rendered"]
                    and result["clean_event_loop_exit"]
                    and not result["shutdown_failed"]
                )
            else:
                result["success"] = (
                    result.get("returncode") == 0
                    and result.get("closed") is True
                    and result.get("first_content_paint") is True
                    and result.get("color_count", 0) >= 10
                    and result.get("blank_color_fraction", 1) < 0.95
                )
            failures += not result["success"]
            output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            print(
                f"{arguments.label} {index + 1}/{arguments.count} {'PASS' if result['success'] else 'FAIL'}",
                flush=True,
            )
    print(
        json.dumps(
            {
                "count": arguments.count,
                "failures": failures,
                "elapsed_s": round(time.perf_counter() - started, 1),
                "results": str(results),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
