#!/usr/bin/env python3
"""Compare multilingual faster-whisper models on fixed tuning files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class Candidate:
    model_id: str
    path: Path
    compute_type: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--references-dir", type=Path, required=True)
    parser.add_argument("--split-file", type=Path, required=True)
    parser.add_argument("--vad-model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        metavar="MODEL_ID|PATH|COMPUTE_TYPE",
    )
    return parser


def _candidate(value: str) -> Candidate:
    parts = value.split("|", 2)
    if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
        raise ValueError(f"invalid candidate: {value}")
    path = Path(parts[1]).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"candidate model directory not found: {path}")
    return Candidate(parts[0].strip(), path, parts[2].strip())


def _audio(directory: Path, identifier: str) -> Path:
    matches = sorted(directory.glob(f"{identifier}_voice_*.ogg"))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one audio file for {identifier}, found {len(matches)}")
    return matches[0].resolve()


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _run(
    arguments: argparse.Namespace,
    candidate: Candidate,
    audio: tuple[Path, ...],
    split: str,
    suffix: str,
) -> dict[str, Any]:
    run_name = f"model-screen-{suffix}-{candidate.model_id}"
    runner = Path(__file__).with_name("run_accuracy_matrix.py").resolve()
    command = [
        str(arguments.python.resolve()),
        str(runner),
        "--audio",
        *(str(path) for path in audio),
        "--references-dir",
        str(arguments.references_dir.resolve()),
        "--split-file",
        str(arguments.split_file.resolve()),
        "--split",
        split,
        "--backend",
        "faster-whisper-direct",
        "--model",
        str(candidate.path),
        "--model-id",
        candidate.model_id,
        "--compute-type",
        candidate.compute_type,
        "--vad-model",
        str(arguments.vad_model.resolve()),
        "--language",
        "ru",
        "--beam-size",
        "1",
        "--best-of",
        "1",
        "--direct-vad",
        "--end-silence-ms",
        "500",
        "--output-dir",
        str(arguments.output_dir.resolve()),
        "--run-name",
        run_name,
    ]
    subprocess.run(command, check=True)
    matches = sorted(
        arguments.output_dir.glob(f"{run_name}-*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise RuntimeError(f"missing model screening output: {run_name}")
    report = json.loads((matches[0] / "report.json").read_text(encoding="utf-8"))
    return cast(dict[str, Any], report)


def main() -> int:
    arguments = _parser().parse_args()
    candidates = tuple(_candidate(value) for value in arguments.candidate)
    if len({candidate.model_id for candidate in candidates}) != len(candidates):
        raise ValueError("candidate model ids must be unique")
    tuning = tuple(_audio(arguments.audio_dir, identifier) for identifier in ("001", "004", "005"))
    held_out = (_audio(arguments.audio_dir, "009"),)
    tuning_reports = {
        candidate.model_id: _run(arguments, candidate, tuning, "tuning", "tuning")
        for candidate in candidates
    }
    balanced_pool = [
        candidate
        for candidate in candidates
        if float(
            tuning_reports[candidate.model_id]["aggregate"].get(
                "wall_rtf", tuning_reports[candidate.model_id]["aggregate"]["rtf"]
            )
        )
        <= 0.85
    ]
    if not balanced_pool:
        balanced_pool = list(candidates)
    balanced = min(
        balanced_pool,
        key=lambda candidate: (
            float(tuning_reports[candidate.model_id]["aggregate"]["pseudo_wer"]),
            float(
                tuning_reports[candidate.model_id]["aggregate"].get(
                    "wall_rtf", tuning_reports[candidate.model_id]["aggregate"]["rtf"]
                )
            ),
        ),
    )
    maximum = min(
        candidates,
        key=lambda candidate: (
            float(tuning_reports[candidate.model_id]["aggregate"]["pseudo_wer"]),
            float(
                tuning_reports[candidate.model_id]["aggregate"].get(
                    "wall_rtf", tuning_reports[candidate.model_id]["aggregate"]["rtf"]
                )
            ),
        ),
    )
    held_candidates = {balanced.model_id: balanced, maximum.model_id: maximum}
    held_reports = {
        model_id: _run(arguments, candidate, held_out, "held_out", "held")
        for model_id, candidate in held_candidates.items()
    }
    selection = {
        "tuning_only_selection": True,
        "tuning": {model_id: report["aggregate"] for model_id, report in tuning_reports.items()},
        "balanced": balanced.model_id,
        "maximum_accuracy": maximum.model_id,
        "held_out_observation": {
            model_id: report["aggregate"] for model_id, report in held_reports.items()
        },
    }
    _write_json(arguments.output_dir / "model-screening-selection.json", selection)
    print(json.dumps(selection, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
