#!/usr/bin/env python3
"""Run a bounded, sequential small-model screening matrix.

Selection uses tuning files only.  The held-out observation is run exactly once
after the winning production configuration is fixed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--references-dir", type=Path, required=True)
    parser.add_argument("--split-file", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--vad-model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    return parser


def _audio(directory: Path, identifier: str) -> Path:
    matches = sorted(directory.glob(f"{identifier}_voice_*.ogg"))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one audio file for {identifier}, found {len(matches)}")
    return matches[0].resolve()


def _base(arguments: argparse.Namespace) -> list[str]:
    runner = Path(__file__).with_name("run_accuracy_matrix.py").resolve()
    return [
        str(arguments.python.resolve()),
        str(runner),
        "--references-dir",
        str(arguments.references_dir.resolve()),
        "--split-file",
        str(arguments.split_file.resolve()),
        "--model",
        str(arguments.model.resolve()),
        "--model-id",
        "small",
        "--compute-type",
        "int8",
        "--vad-model",
        str(arguments.vad_model.resolve()),
        "--language",
        "ru",
        "--beam-size",
        "1",
        "--best-of",
        "1",
        "--output-dir",
        str(arguments.output_dir.resolve()),
    ]


def _run(
    arguments: argparse.Namespace,
    name: str,
    audio: tuple[Path, ...],
    split: str,
    options: tuple[str, ...],
) -> dict[str, Any]:
    command = [
        *_base(arguments),
        "--audio",
        *(str(path) for path in audio),
        "--split",
        split,
        "--run-name",
        name,
        *options,
    ]
    subprocess.run(command, check=True)
    matches = sorted(
        arguments.output_dir.glob(f"{name}-*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise RuntimeError(f"runner did not create output for {name}")
    report = json.loads((matches[0] / "report.json").read_text(encoding="utf-8"))
    if not report.get("complete"):
        raise RuntimeError(f"incomplete output for {name}")
    return cast(dict[str, Any], report)


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    arguments = _parser().parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    worst = (_audio(arguments.audio_dir, "005"),)
    tuning_screen = tuple(
        _audio(arguments.audio_dir, identifier) for identifier in ("001", "004", "005")
    )
    held_out = (_audio(arguments.audio_dir, "009"),)
    production_candidates: dict[str, tuple[str, ...]] = {
        "prod-prompt20": ("--backend", "production", "--prompt-words", "20"),
        "prod-prompt40": ("--backend", "production", "--prompt-words", "40"),
        "prod-short": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "8",
            "--maximum-segment-seconds",
            "12",
            "--overlap-seconds",
            "0.8",
            "--end-silence-ms",
            "500",
        ),
        "prod-long": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "15",
            "--maximum-segment-seconds",
            "25",
            "--overlap-seconds",
            "0.8",
        ),
        "prod-long-silence300": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "15",
            "--maximum-segment-seconds",
            "25",
            "--overlap-seconds",
            "0.8",
            "--end-silence-ms",
            "300",
        ),
        "prod-long-silence900": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "15",
            "--maximum-segment-seconds",
            "25",
            "--overlap-seconds",
            "0.8",
            "--end-silence-ms",
            "900",
        ),
        "prod-long-raw": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "15",
            "--maximum-segment-seconds",
            "25",
            "--overlap-seconds",
            "0.8",
            "--preprocessing",
            "raw",
        ),
        "prod-long-quiet": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "15",
            "--maximum-segment-seconds",
            "25",
            "--overlap-seconds",
            "0.8",
            "--preprocessing",
            "quiet_voice",
        ),
        "prod-long-noisy": (
            "--backend",
            "production",
            "--prompt-words",
            "30",
            "--preferred-segment-seconds",
            "15",
            "--maximum-segment-seconds",
            "25",
            "--overlap-seconds",
            "0.8",
            "--preprocessing",
            "noisy_room",
        ),
    }
    phase_one: dict[str, dict[str, Any]] = {}
    for name, options in production_candidates.items():
        phase_one[name] = _run(arguments, f"screen-005-{name}", worst, "tuning", options)
    eligible = [
        (name, report)
        for name, report in phase_one.items()
        if float(report["aggregate"].get("wall_rtf", report["aggregate"]["rtf"])) <= 0.85
    ]
    if not eligible:
        eligible = list(phase_one.items())
    top_names = [
        name
        for name, _report in sorted(
            eligible,
            key=lambda item: (
                float(item[1]["aggregate"]["pseudo_wer"]),
                float(item[1]["aggregate"].get("wall_rtf", item[1]["aggregate"]["rtf"])),
            ),
        )[:3]
    ]
    tuning_reports = {
        name: _run(
            arguments,
            f"screen-tuning-{name}",
            tuning_screen,
            "tuning",
            production_candidates[name],
        )
        for name in top_names
    }
    direct_reports = {
        "direct": _run(
            arguments,
            "screen-tuning-direct",
            tuning_screen,
            "tuning",
            ("--backend", "faster-whisper-direct"),
        ),
        "direct-vad500": _run(
            arguments,
            "screen-tuning-direct-vad500",
            tuning_screen,
            "tuning",
            (
                "--backend",
                "faster-whisper-direct",
                "--direct-vad",
                "--end-silence-ms",
                "500",
            ),
        ),
    }
    winner_name, winner_report = min(
        tuning_reports.items(),
        key=lambda item: (
            float(item[1]["aggregate"]["pseudo_wer"]),
            float(item[1]["aggregate"].get("wall_rtf", item[1]["aggregate"]["rtf"])),
        ),
    )
    held_report = _run(
        arguments,
        f"screen-held-{winner_name}",
        held_out,
        "held_out",
        production_candidates[winner_name],
    )
    selection = {
        "selection_rule": "minimum tuning pseudo-WER among production RTF <= 0.85",
        "held_out_used_for_selection": False,
        "phase_one": {name: report["aggregate"] for name, report in phase_one.items()},
        "tuning_finalists": {name: report["aggregate"] for name, report in tuning_reports.items()},
        "direct_comparators": {
            name: report["aggregate"] for name, report in direct_reports.items()
        },
        "winner": winner_name,
        "winner_options": production_candidates[winner_name],
        "winner_tuning": winner_report["aggregate"],
        "winner_held_out_observation": held_report["aggregate"],
    }
    _write_json(arguments.output_dir / "screening-selection.json", selection)
    print(json.dumps(selection, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
