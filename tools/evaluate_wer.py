#!/usr/bin/env python3
"""Transcribe one personal reference file and report raw/normalized WER and RTF."""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil
from faster_whisper import WhisperModel
from platformdirs import user_data_path

from voxtype.asr.profiles import ASRProfile, ASRProfileConfig, get_profile
from voxtype.constants import APP_AUTHOR, APP_NAME

_PUNCTUATION = re.compile(r"[^\w\s-]", re.UNICODE)
_NUMBER_WORDS = {
    "ноль": "0",
    "один": "1",
    "одна": "1",
    "два": "2",
    "две": "2",
    "три": "3",
    "четыре": "4",
    "пять": "5",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
}


@dataclass(frozen=True, slots=True)
class ErrorCounts:
    substitutions: int
    deletions: int
    insertions: int
    reference_words: int

    @property
    def wer(self) -> float:
        errors = self.substitutions + self.deletions + self.insertions
        return errors / self.reference_words if self.reference_words else float(errors > 0)


def normalize(text: str, *, fold_yo: bool, normalize_numbers: bool) -> list[str]:
    cleaned = text.casefold()
    if fold_yo:
        cleaned = cleaned.replace("ё", "е")
    cleaned = _PUNCTUATION.sub(" ", cleaned)
    words = cleaned.split()
    if normalize_numbers:
        words = [_NUMBER_WORDS.get(word, word) for word in words]
    return words


def error_counts(reference: list[str], hypothesis: list[str]) -> ErrorCounts:
    rows = len(reference) + 1
    columns = len(hypothesis) + 1
    cost: list[list[tuple[int, int, int, int]]] = [
        [(0, 0, 0, 0) for _ in range(columns)] for _ in range(rows)
    ]
    for row in range(1, rows):
        cost[row][0] = (row, 0, row, 0)
    for column in range(1, columns):
        cost[0][column] = (column, 0, 0, column)
    for row in range(1, rows):
        for column in range(1, columns):
            if reference[row - 1] == hypothesis[column - 1]:
                cost[row][column] = cost[row - 1][column - 1]
                continue
            diagonal = cost[row - 1][column - 1]
            deletion = cost[row - 1][column]
            insertion = cost[row][column - 1]
            candidates = (
                (diagonal[0] + 1, diagonal[1] + 1, diagonal[2], diagonal[3]),
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
            )
            # For equal edit distance, count a direct replacement as one
            # substitution instead of the less informative delete+insert pair.
            cost[row][column] = min(
                candidates, key=lambda item: (item[0], item[2] + item[3], item[1])
            )
    _, substitutions, deletions, insertions = cost[-1][-1]
    return ErrorCounts(substitutions, deletions, insertions, len(reference))


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        return stream.getnframes() / stream.getframerate()


def resolve_profile(profile: str) -> ASRProfileConfig:
    """Resolve the CLI profile through the production ASR profile registry."""

    return get_profile(ASRProfile(profile))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("reference", type=Path, help="UTF-8 reference transcript")
    parser.add_argument("--language", choices=("ru", "en"), required=True)
    parser.add_argument(
        "--profile", choices=("fast", "balanced", "maximum_accuracy"), required=True
    )
    parser.add_argument("--model", default=None, help="Override profile model name/path")
    parser.add_argument("--keep-yo", action="store_true")
    parser.add_argument("--normalize-numbers", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()
    profile = resolve_profile(arguments.profile)
    model_id = profile.model_name
    model_name = arguments.model or str(user_data_path(APP_NAME, APP_AUTHOR) / "models" / model_id)
    reference_text = arguments.reference.read_text(encoding="utf-8").strip()
    process = psutil.Process()
    rss_start = process.memory_info().rss
    rss_samples = [rss_start]
    sampling_done = threading.Event()

    def sample_memory() -> None:
        while not sampling_done.wait(0.05):
            rss_samples.append(process.memory_info().rss)

    sampler = threading.Thread(target=sample_memory, name="voxtype-wer-memory", daemon=True)
    sampler.start()
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type=profile.compute_type,
        cpu_threads=profile.cpu_threads,
        local_files_only=True,
    )
    started = time.perf_counter()
    segments, _info = model.transcribe(
        str(arguments.audio),
        language=arguments.language,
        task="transcribe",
        temperature=0.0,
        beam_size=profile.beam_size,
        best_of=profile.best_of,
        word_timestamps=profile.word_timestamps,
        condition_on_previous_text=profile.condition_on_previous_text,
        vad_filter=False,
        no_speech_threshold=profile.no_speech_threshold,
        log_prob_threshold=profile.log_probability_threshold,
        compression_ratio_threshold=profile.compression_ratio_threshold,
    )
    hypothesis = " ".join(segment.text.strip() for segment in segments).strip()
    processing_s = time.perf_counter() - started
    sampling_done.set()
    sampler.join(timeout=1.0)
    rss_samples.append(process.memory_info().rss)
    duration_s = wav_duration(arguments.audio)
    raw = error_counts(reference_text.split(), hypothesis.split())
    normalized = error_counts(
        normalize(
            reference_text,
            fold_yo=not arguments.keep_yo,
            normalize_numbers=arguments.normalize_numbers,
        ),
        normalize(
            hypothesis,
            fold_yo=not arguments.keep_yo,
            normalize_numbers=arguments.normalize_numbers,
        ),
    )
    report = {
        "audio": str(arguments.audio.resolve()),
        "language": arguments.language,
        "profile": arguments.profile,
        "model": model_name,
        "reference_raw": reference_text,
        "hypothesis_raw": hypothesis,
        "raw": {**asdict(raw), "wer": raw.wer},
        "normalized": {**asdict(normalized), "wer": normalized.wer},
        "audio_duration_s": duration_s,
        "processing_s": processing_s,
        "rtf": processing_s / duration_s if duration_s else 0.0,
        "rss_start_bytes": rss_start,
        "rss_peak_bytes": max(rss_samples),
        "rss_delta_bytes": max(rss_samples) - rss_start,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
