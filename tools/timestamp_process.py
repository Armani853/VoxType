"""Run a build command and retain timestamped output for phase accounting."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a build command is required")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with args.log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            elapsed = time.perf_counter() - started
            output.write(f"{elapsed:.3f}\t{line}")
            output.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
