#!/usr/bin/env python3
"""Run reproducible private ASR experiments through direct or production paths.

The supplied reference may be another recognizer's output.  Metrics emitted by
this tool are therefore named pseudo-WER and must not be presented as
human-reviewed accuracy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean
from typing import Any

import psutil
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from voxtype.asr.base import TranscriptionRequest, bounded_prompt
from voxtype.asr.faster_whisper_backend import FasterWhisperBackend
from voxtype.asr.hallucination_guard import HallucinationGuard
from voxtype.asr.profiles import ASRProfile, ASRProfileConfig
from voxtype.audio.preprocessing import (
    AudioPreprocessor,
    PreprocessingProfile,
    PreprocessorSettings,
)
from voxtype.core.models import Language, TranscriptFragment, WordStamp
from voxtype.transcript.merger import TranscriptMerger
from voxtype.vad.segmenter import AudioSegment, SegmentationSettings, StreamingSegmenter
from voxtype.vad.silero import SileroVAD

RUNNER_VERSION = 5
_TOKEN = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-'][0-9A-Za-zА-Яа-яЁё]+)*", re.UNICODE)
_FILE_ID = re.compile(r"^(\d{3})")


@dataclass(frozen=True, slots=True)
class EditCounts:
    substitutions: int
    deletions: int
    insertions: int
    reference_units: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def rate(self) -> float:
        if self.reference_units:
            return self.errors / self.reference_units
        return float(self.errors > 0)


@dataclass(frozen=True, slots=True)
class RecognitionOutput:
    text: str
    raw_text: str
    segments: tuple[dict[str, Any], ...]
    words: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    processing_seconds: float
    vad_segments: int
    retries: int
    cascade_retries: int
    duplicates: int
    hallucinations: int
    uncertain: int


class ResourceSampler:
    """Sample current-process resources without recording transcript content."""

    def __init__(self, interval_seconds: float = 0.1) -> None:
        self.interval_seconds = interval_seconds
        self.process = psutil.Process()
        self.rss_samples: list[int] = []
        self.cpu_samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._sample,
            name="voxtype-private-eval-resources",
            daemon=True,
        )

    def start(self) -> None:
        self.process.cpu_percent(None)
        self._sample_once()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._sample_once()

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample_once()

    def _sample_once(self) -> None:
        try:
            self.rss_samples.append(self.process.memory_info().rss)
            self.cpu_samples.append(self.process.cpu_percent(None))
        except (psutil.Error, OSError):
            return

    @property
    def peak_rss_bytes(self) -> int:
        return max(self.rss_samples, default=0)

    @property
    def mean_cpu_percent(self) -> float:
        return fmean(self.cpu_samples) if self.cpu_samples else 0.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--audio", type=Path, nargs="+")
    inputs.add_argument("--audio-dir", type=Path)
    parser.add_argument("--pattern", default="*_voice_*.ogg")
    parser.add_argument("--references-dir", type=Path, required=True)
    parser.add_argument("--split-file", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=("tuning", "held_out", "all", "screening"),
        default="tuning",
    )
    parser.add_argument(
        "--backend",
        choices=("faster-whisper-direct", "production"),
        default="production",
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--retry-model", type=Path)
    parser.add_argument("--retry-model-id")
    parser.add_argument("--retry-compute-type", default="int8")
    parser.add_argument("--vad-model", type=Path)
    parser.add_argument("--language", choices=("ru", "en"), required=True)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--best-of", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--condition-on-previous-text", action="store_true")
    parser.add_argument("--no-speech-threshold", type=float, default=0.55)
    parser.add_argument("--log-prob-threshold", type=float, default=-1.0)
    parser.add_argument("--compression-ratio-threshold", type=float, default=2.4)
    parser.add_argument("--repetition-penalty", type=float, default=1.0)
    parser.add_argument("--no-repeat-ngram-size", type=int, default=0)
    parser.add_argument("--direct-vad", action="store_true")
    parser.add_argument("--speech-threshold", type=float, default=0.45)
    parser.add_argument("--minimum-speech-ms", type=int, default=180)
    parser.add_argument("--end-silence-ms", type=int, default=650)
    parser.add_argument("--pre-roll-ms", type=int, default=400)
    parser.add_argument("--post-roll-ms", type=int, default=300)
    parser.add_argument("--preferred-segment-seconds", type=float, default=12.0)
    parser.add_argument("--maximum-segment-seconds", type=float, default=18.0)
    parser.add_argument("--overlap-seconds", type=float, default=1.2)
    parser.add_argument("--forced-pause-ms", type=int, default=180)
    parser.add_argument(
        "--preprocessing",
        choices=("raw", "normal", "quiet_voice", "noisy_room"),
        default="normal",
    )
    parser.add_argument("--prompt-words", type=int, default=0)
    parser.add_argument("--vocabulary-file", type=Path)
    parser.add_argument("--disable-guard", action="store_true")
    parser.add_argument("--disable-merger", action="store_true")
    parser.add_argument("--disable-formatting", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--force", action="store_true")
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _normalize_words(text: str) -> list[str]:
    normalized = text.casefold().replace("ё", "е")
    return [match.group(0) for match in _TOKEN.finditer(normalized)]


def _normalized_characters(text: str) -> str:
    return "".join(_normalize_words(text))


def _edit_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> EditCounts:
    previous = [(index, 0, 0, index) for index in range(len(hypothesis) + 1)]
    for ref_index, ref_unit in enumerate(reference, start=1):
        current = [(ref_index, 0, ref_index, 0)]
        for hyp_index, hyp_unit in enumerate(hypothesis, start=1):
            if ref_unit == hyp_unit:
                current.append(previous[hyp_index - 1])
                continue
            diagonal = previous[hyp_index - 1]
            deletion = previous[hyp_index]
            insertion = current[hyp_index - 1]
            candidates = (
                (diagonal[0] + 1, diagonal[1] + 1, diagonal[2], diagonal[3]),
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
            )
            current.append(min(candidates, key=lambda item: (item[0], item[2] + item[3])))
        previous = current
    _, substitutions, deletions, insertions = previous[-1]
    return EditCounts(substitutions, deletions, insertions, len(reference))


def _levenshtein_distance(reference: str, hypothesis: str) -> int:
    # Myers' bit-parallel algorithm is exact and avoids a multi-million-cell
    # Python loop for character error rate on long recordings.
    if len(reference) > len(hypothesis):
        reference, hypothesis = hypothesis, reference
    if not reference:
        return len(hypothesis)
    character_masks: dict[str, int] = {}
    for index, character in enumerate(reference):
        character_masks[character] = character_masks.get(character, 0) | (1 << index)
    positive = ~0
    negative = 0
    score = len(reference)
    last = 1 << (len(reference) - 1)
    for character in hypothesis:
        equal = character_masks.get(character, 0)
        combined = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        positive_horizontal = negative | ~(horizontal | positive)
        negative_horizontal = positive & horizontal
        if positive_horizontal & last:
            score += 1
        elif negative_horizontal & last:
            score -= 1
        positive_horizontal = (positive_horizontal << 1) | 1
        negative_horizontal <<= 1
        positive = negative_horizontal | ~(combined | positive_horizontal)
        negative = positive_horizontal & combined
    return score


def _file_id(path: Path) -> str:
    match = _FILE_ID.match(path.stem)
    if match is None:
        raise ValueError(f"audio filename must start with a three-digit id: {path.name}")
    return match.group(1)


def _reference_for(audio: Path, directory: Path) -> Path:
    exact = directory / f"{audio.stem}.txt"
    if exact.is_file():
        return exact
    matches = sorted(directory.glob(f"{_file_id(audio)}*.txt"))
    if len(matches) != 1:
        raise FileNotFoundError(f"one reference is required for {audio.name}, found {len(matches)}")
    return matches[0]


def _resolve_audio(arguments: argparse.Namespace) -> tuple[Path, ...]:
    files = (
        tuple(arguments.audio)
        if arguments.audio
        else tuple(sorted(arguments.audio_dir.glob(arguments.pattern)))
    )
    if not files:
        raise FileNotFoundError("no input audio files matched")
    split = json.loads(arguments.split_file.read_text(encoding="utf-8-sig"))
    if arguments.split == "all":
        allowed = set(split["tuning"]) | set(split["held_out"])
    elif arguments.split == "screening":
        allowed = set(split["screening"].values())
    else:
        allowed = set(split[arguments.split])
    selected = tuple(path.resolve() for path in files if _file_id(path) in allowed)
    if not selected:
        raise FileNotFoundError(f"no files selected for split {arguments.split}")
    return selected


def _config(arguments: argparse.Namespace, audio_files: Sequence[Path]) -> dict[str, Any]:
    excluded = {"force", "audio", "audio_dir", "output_dir", "run_name"}
    options = {
        key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in vars(arguments).items()
        if key not in excluded
    }
    inputs = []
    for audio in audio_files:
        reference = _reference_for(audio, arguments.references_dir)
        inputs.append(
            {
                "id": _file_id(audio),
                "audio": str(audio),
                "audio_sha256": _sha256(audio),
                "reference": str(reference.resolve()),
                "reference_sha256": _sha256(reference),
            }
        )
    model_manifests = [
        path
        for path in (
            arguments.model / ".voxtype-model.json",
            arguments.model / "manifest.json",
        )
        if path.is_file()
    ]
    runtime_identity = {
        "split_sha256": _sha256(arguments.split_file),
        "vad_sha256": _sha256(arguments.vad_model)
        if arguments.vad_model and arguments.vad_model.is_file()
        else None,
        "vocabulary_sha256": _sha256(arguments.vocabulary_file)
        if arguments.vocabulary_file and arguments.vocabulary_file.is_file()
        else None,
        "model_manifest": str(model_manifests[0].resolve()) if model_manifests else None,
        "model_manifest_sha256": _sha256(model_manifests[0]) if model_manifests else None,
    }
    return {
        "runner_version": RUNNER_VERSION,
        "options": options,
        "runtime_identity": runtime_identity,
        "inputs": inputs,
    }


def _config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preprocessor_settings(name: str) -> PreprocessorSettings:
    if name == "raw":
        return PreprocessorSettings.for_profile(PreprocessingProfile.RAW)
    if name == "noisy_room":
        return PreprocessorSettings.for_profile(PreprocessingProfile.NOISY_ROOM)
    if name == "quiet_voice":
        return replace(
            PreprocessorSettings.for_profile(PreprocessingProfile.NORMAL),
            target_rms_dbfs=-20.0,
            maximum_gain_db=20.0,
        )
    return PreprocessorSettings.for_profile(PreprocessingProfile.NORMAL)


def _segment_payload(segment: Any) -> dict[str, Any]:
    return {
        "start": float(segment.start),
        "end": float(segment.end),
        "text": str(segment.text),
        "average_log_probability": _optional_float(segment.avg_logprob),
        "no_speech_probability": _optional_float(segment.no_speech_prob),
        "compression_ratio": _optional_float(segment.compression_ratio),
    }


def _word_payload(word: Any) -> dict[str, Any]:
    return {
        "word": str(word.word),
        "start": float(word.start),
        "end": float(word.end),
        "probability": _optional_float(word.probability),
    }


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _run_direct(model: Any, audio_path: Path, arguments: argparse.Namespace) -> RecognitionOutput:
    started = time.perf_counter()
    raw_segments, _info = model.transcribe(
        str(audio_path),
        language=arguments.language,
        task="transcribe",
        temperature=arguments.temperature,
        beam_size=arguments.beam_size,
        best_of=arguments.best_of,
        word_timestamps=True,
        condition_on_previous_text=arguments.condition_on_previous_text,
        vad_filter=arguments.direct_vad,
        vad_parameters=(
            {"min_silence_duration_ms": arguments.end_silence_ms} if arguments.direct_vad else None
        ),
        no_speech_threshold=arguments.no_speech_threshold,
        log_prob_threshold=arguments.log_prob_threshold,
        compression_ratio_threshold=arguments.compression_ratio_threshold,
        repetition_penalty=arguments.repetition_penalty,
        no_repeat_ngram_size=arguments.no_repeat_ngram_size,
    )
    materialized = tuple(raw_segments)
    elapsed = time.perf_counter() - started
    segments = tuple(_segment_payload(segment) for segment in materialized)
    words = tuple(
        _word_payload(word)
        for segment in materialized
        for word in (getattr(segment, "words", None) or ())
    )
    text = " ".join(str(segment.text).strip() for segment in materialized).strip()
    return RecognitionOutput(
        text=text,
        raw_text=text,
        segments=segments,
        words=words,
        warnings=(),
        processing_seconds=elapsed,
        vad_segments=len(materialized) if arguments.direct_vad else 0,
        retries=0,
        cascade_retries=0,
        duplicates=0,
        hallucinations=0,
        uncertain=0,
    )


def _run_production(
    backend: FasterWhisperBackend,
    retry_backend: FasterWhisperBackend | None,
    audio_path: Path,
    arguments: argparse.Namespace,
    vocabulary: tuple[str, ...],
) -> RecognitionOutput:
    decoded = decode_audio(str(audio_path), sampling_rate=16_000)
    preprocessor = AudioPreprocessor(settings=_preprocessor_settings(arguments.preprocessing))
    detector = SileroVAD(arguments.vad_model, strict=True)
    segmenter = StreamingSegmenter(
        detector,
        settings=SegmentationSettings(
            speech_threshold=arguments.speech_threshold,
            minimum_speech_ms=arguments.minimum_speech_ms,
            end_silence_ms=arguments.end_silence_ms,
            pre_roll_ms=arguments.pre_roll_ms,
            post_roll_ms=arguments.post_roll_ms,
            preferred_segment_seconds=arguments.preferred_segment_seconds,
            maximum_segment_seconds=arguments.maximum_segment_seconds,
            overlap_seconds=arguments.overlap_seconds,
            forced_pause_ms=arguments.forced_pause_ms,
        ),
    )
    audio_segments: list[AudioSegment] = []
    for offset in range(0, decoded.size, 480):
        processed = preprocessor.process(decoded[offset : offset + 480]).samples
        audio_segments.extend(segmenter.process(processed))
    audio_segments.extend(segmenter.flush())

    guard = HallucinationGuard()
    merger = TranscriptMerger()
    raw_parts: list[str] = []
    segment_payloads: list[dict[str, Any]] = []
    word_payloads: list[dict[str, Any]] = []
    warnings: list[str] = []
    retries = 0
    cascade_retries = 0
    duplicates = 0
    hallucinations = 0
    uncertain = 0
    processing_seconds = 0.0
    language = Language.RU if arguments.language == "ru" else Language.EN
    previous_segment_end: float | None = None
    previous_uncertain = False

    for audio_segment in audio_segments:
        continuing = (
            previous_segment_end is None
            or audio_segment.start_seconds - previous_segment_end <= 8.0
        )
        context = (
            merger.committed_text
            if (
                continuing
                and arguments.prompt_words
                and not previous_uncertain
                and not merger.committed_text.rstrip().endswith((".", "!", "?", "…"))
            )
            else ""
        )
        prompt = (
            bounded_prompt(
                context,
                vocabulary,
                max_context_words=arguments.prompt_words,
            )
            if arguments.prompt_words or vocabulary
            else ""
        )
        previous_segment_end = audio_segment.end_seconds
        request = TranscriptionRequest(
            audio=audio_segment.samples,
            sample_rate=audio_segment.sample_rate,
            language=arguments.language,
            start_offset_seconds=audio_segment.start_seconds,
            initial_prompt=prompt,
            beam_size=arguments.beam_size,
            temperature=arguments.temperature,
            condition_on_previous_text=arguments.condition_on_previous_text,
            no_speech_threshold=arguments.no_speech_threshold,
            log_probability_threshold=arguments.log_prob_threshold,
            compression_ratio_threshold=arguments.compression_ratio_threshold,
            metadata={"segment_sequence": str(audio_segment.sequence)},
        )
        if arguments.disable_guard:
            result = backend.transcribe(request)
        else:
            guarded = guard.transcribe_guarded(
                backend,
                request,
                committed_text=merger.committed_text,
                retry_backend=retry_backend,
            )
            result = guarded.result
            retries += int(guarded.retried)
            cascade_retries += int(
                guarded.retry_model_id is not None and guarded.retry_model_id != backend.model_id
            )
            hallucinations += int(guarded.first_assessment.suspicious)
        processing_seconds += result.processing_seconds
        uncertain += int(result.possible_inaccuracy)
        previous_uncertain = result.possible_inaccuracy
        warnings.extend(result.warnings)
        raw_parts.append(result.text.strip())
        for segment in result.segments:
            segment_payloads.append(
                {
                    "sequence": audio_segment.sequence,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "average_log_probability": segment.average_log_probability,
                    "no_speech_probability": segment.no_speech_probability,
                    "compression_ratio": segment.compression_ratio,
                    "vad_reason": audio_segment.reason.value,
                }
            )
        word_payloads.extend(asdict(word) for word in result.words)
        if arguments.disable_merger:
            continue
        log_probabilities = [
            item.average_log_probability
            for item in result.segments
            if item.average_log_probability is not None
        ]
        compression_ratios = [
            item.compression_ratio for item in result.segments if item.compression_ratio is not None
        ]
        no_speech_probabilities = [
            item.no_speech_probability
            for item in result.segments
            if item.no_speech_probability is not None
        ]
        fragment = TranscriptFragment(
            sequence=audio_segment.sequence,
            text=result.text,
            words=tuple(
                WordStamp(word.word, word.start, word.end, word.probability or 0.0)
                for word in result.words
            ),
            chunk_start_s=audio_segment.start_seconds,
            chunk_end_s=audio_segment.end_seconds,
            language=language,
            average_log_probability=(fmean(log_probabilities) if log_probabilities else 0.0),
            compression_ratio=max(compression_ratios, default=1.0),
            no_speech_probability=max(no_speech_probabilities, default=0.0),
            inference_s=result.processing_seconds,
        )
        outcome = merger.merge(
            fragment,
            add_space=not arguments.disable_formatting,
            capitalize=not arguments.disable_formatting,
        )
        duplicates += int(outcome.duplicate_detected)

    raw_text = " ".join(part for part in raw_parts if part).strip()
    final_text = raw_text if arguments.disable_merger else merger.committed_text
    return RecognitionOutput(
        text=final_text,
        raw_text=raw_text,
        segments=tuple(segment_payloads),
        words=tuple(word_payloads),
        warnings=tuple(dict.fromkeys(warnings)),
        processing_seconds=processing_seconds,
        vad_segments=len(audio_segments),
        retries=retries,
        cascade_retries=cascade_retries,
        duplicates=duplicates,
        hallucinations=hallucinations,
        uncertain=uncertain,
    )


def _score(reference_text: str, hypothesis: str) -> dict[str, Any]:
    reference_words = _normalize_words(reference_text)
    hypothesis_words = _normalize_words(hypothesis)
    word_errors = _edit_counts(reference_words, hypothesis_words)
    reference_characters = _normalized_characters(reference_text)
    hypothesis_characters = _normalized_characters(hypothesis)
    character_errors = _levenshtein_distance(reference_characters, hypothesis_characters)
    return {
        "substitutions": word_errors.substitutions,
        "deletions": word_errors.deletions,
        "insertions": word_errors.insertions,
        "reference_words": word_errors.reference_units,
        "hypothesis_words": len(hypothesis_words),
        "pseudo_wer": word_errors.rate,
        "word_agreement": 1.0 - word_errors.rate,
        "character_errors": character_errors,
        "reference_characters": len(reference_characters),
        "cer": character_errors / len(reference_characters)
        if reference_characters
        else float(character_errors > 0),
    }


def _load_vocabulary(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _update_summary(root: Path, record: dict[str, Any]) -> None:
    json_path = root / "summary.json"
    csv_path = root / "summary.csv"
    try:
        records = json.loads(json_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        records = []
    records = [item for item in records if item.get("config_hash") != record["config_hash"]]
    records.append(record)
    records.sort(key=lambda item: (item.get("created_at", ""), item["config_hash"]))
    _write_json(json_path, records)
    fieldnames = sorted({key for item in records for key in item})
    temporary = csv_path.with_name(f".{csv_path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(csv_path)


def _aggregate(file_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reference_words = sum(int(item["reference_words"]) for item in file_results)
    substitutions = sum(int(item["substitutions"]) for item in file_results)
    deletions = sum(int(item["deletions"]) for item in file_results)
    insertions = sum(int(item["insertions"]) for item in file_results)
    character_errors = sum(int(item["character_errors"]) for item in file_results)
    reference_characters = sum(int(item["reference_characters"]) for item in file_results)
    audio_seconds = sum(float(item["audio_seconds"]) for item in file_results)
    processing_seconds = sum(float(item["processing_seconds"]) for item in file_results)
    wall_seconds = sum(float(item["wall_seconds"]) for item in file_results)
    vad_segments = sum(int(item["vad_segments"]) for item in file_results)
    retries = sum(int(item["retries"]) for item in file_results)
    cascade_retries = sum(int(item.get("cascade_retries", 0)) for item in file_results)
    return {
        "files": len(file_results),
        "audio_seconds": audio_seconds,
        "processing_seconds": processing_seconds,
        "rtf": processing_seconds / audio_seconds if audio_seconds else 0.0,
        "wall_seconds": wall_seconds,
        "wall_rtf": wall_seconds / audio_seconds if audio_seconds else 0.0,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "reference_words": reference_words,
        "pseudo_wer": (substitutions + deletions + insertions) / reference_words
        if reference_words
        else 0.0,
        "cer": character_errors / reference_characters if reference_characters else 0.0,
        "vad_segments": vad_segments,
        "retries": retries,
        "retry_percentage": retries * 100.0 / vad_segments if vad_segments else 0.0,
        "cascade_retries": cascade_retries,
        "cascade_retry_percentage": (
            cascade_retries * 100.0 / vad_segments if vad_segments else 0.0
        ),
        "duplicates": sum(int(item["duplicates"]) for item in file_results),
        "hallucinations": sum(int(item["hallucinations"]) for item in file_results),
        "uncertain": sum(int(item["uncertain"]) for item in file_results),
    }


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.backend == "production" and arguments.vad_model is None:
        raise ValueError("--vad-model is required for the production backend")
    if (arguments.retry_model is None) != (arguments.retry_model_id is None):
        raise ValueError("--retry-model and --retry-model-id must be supplied together")
    if arguments.retry_model is not None and arguments.backend != "production":
        raise ValueError("--retry-model is supported only by the production backend")
    if not 0 <= arguments.prompt_words <= 40:
        raise ValueError("--prompt-words must be between 0 and 40")
    audio_files = _resolve_audio(arguments)
    config = _config(arguments, audio_files)
    config_hash = _config_hash(config)
    prefix = f"{arguments.run_name}-" if arguments.run_name else ""
    run_directory = arguments.output_dir / f"{prefix}{config_hash[:12]}"
    report_path = run_directory / "report.json"
    if report_path.is_file() and not arguments.force:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("complete") and report.get("config_hash") == config_hash:
            print(f"cache-hit {run_directory}")
            return 0

    run_directory.mkdir(parents=True, exist_ok=True)
    _write_json(run_directory / "config.json", {**config, "config_hash": config_hash})
    sampler = ResourceSampler()
    sampler.start()
    wall_started = time.perf_counter()
    model_load_started = time.perf_counter()
    direct_model: Any = None
    production_backend: FasterWhisperBackend | None = None
    retry_backend: FasterWhisperBackend | None = None
    profile = ASRProfileConfig(
        profile=ASRProfile.FAST,
        model_name=arguments.model_id,
        compute_type=arguments.compute_type,
        beam_size=arguments.beam_size,
        best_of=arguments.best_of,
        cpu_threads=arguments.threads,
        no_speech_threshold=arguments.no_speech_threshold,
        log_probability_threshold=arguments.log_prob_threshold,
        compression_ratio_threshold=arguments.compression_ratio_threshold,
        word_timestamps=True,
        condition_on_previous_text=arguments.condition_on_previous_text,
    )
    if arguments.backend == "faster-whisper-direct":
        direct_model = WhisperModel(
            str(arguments.model),
            device="cpu",
            compute_type=arguments.compute_type,
            cpu_threads=arguments.threads,
            num_workers=1,
            local_files_only=True,
        )
    else:
        production_backend = FasterWhisperBackend(arguments.model, profile)
        production_backend.load()
        if arguments.retry_model is not None:
            retry_profile = replace(
                profile,
                model_name=arguments.retry_model_id,
                compute_type=arguments.retry_compute_type,
                beam_size=1,
                best_of=1,
            )
            retry_backend = FasterWhisperBackend(arguments.retry_model, retry_profile)
    model_load_seconds = time.perf_counter() - model_load_started
    vocabulary = _load_vocabulary(arguments.vocabulary_file)
    file_results: list[dict[str, Any]] = []

    try:
        for index, audio_path in enumerate(audio_files, start=1):
            file_started = time.perf_counter()
            reference_path = _reference_for(audio_path, arguments.references_dir)
            decoded = decode_audio(str(audio_path), sampling_rate=16_000)
            audio_seconds = decoded.size / 16_000
            if arguments.backend == "faster-whisper-direct":
                output = _run_direct(direct_model, audio_path, arguments)
            else:
                if production_backend is None:
                    raise RuntimeError("production backend was not initialized")
                output = _run_production(
                    production_backend,
                    retry_backend,
                    audio_path,
                    arguments,
                    vocabulary,
                )
            wall_seconds = time.perf_counter() - file_started
            reference_text = reference_path.read_text(encoding="utf-8")
            metrics = _score(reference_text, output.text)
            file_directory = run_directory / "files" / audio_path.stem
            _write_text(file_directory / "raw-transcript.txt", output.raw_text + "\n")
            _write_text(
                file_directory / "normalized-transcript.txt",
                " ".join(_normalize_words(output.text)) + "\n",
            )
            _write_json(file_directory / "segments.json", output.segments)
            _write_json(file_directory / "word-timestamps.json", output.words)
            _write_json(file_directory / "warnings.json", output.warnings)
            item = {
                "id": _file_id(audio_path),
                "audio": audio_path.name,
                "audio_seconds": audio_seconds,
                "processing_seconds": output.processing_seconds,
                "wall_seconds": wall_seconds,
                "rtf": output.processing_seconds / audio_seconds if audio_seconds else 0.0,
                "wall_rtf": wall_seconds / audio_seconds if audio_seconds else 0.0,
                "vad_segments": output.vad_segments,
                "retries": output.retries,
                "cascade_retries": output.cascade_retries,
                "duplicates": output.duplicates,
                "hallucinations": output.hallucinations,
                "uncertain": output.uncertain,
                **metrics,
            }
            file_results.append(item)
            _write_json(file_directory / "metrics.json", item)
            _write_json(
                run_directory / "progress.json",
                {
                    "config_hash": config_hash,
                    "completed": index,
                    "total": len(audio_files),
                    "last_id": item["id"],
                    "last_pseudo_wer": item["pseudo_wer"],
                    "last_rtf": item["rtf"],
                    "last_wall_rtf": item["wall_rtf"],
                },
            )
            print(
                f"{item['id']} pseudo-WER={float(item['pseudo_wer']):.4f} "
                f"RTF={float(item['rtf']):.4f}"
            )
    finally:
        if production_backend is not None:
            production_backend.unload()
        if retry_backend is not None:
            retry_backend.unload()
        sampler.stop()

    aggregate = _aggregate(file_results)
    report = {
        "complete": True,
        "metric_kind": "pseudo-WER against unreviewed supplied reference",
        "config_hash": config_hash,
        "run_directory": str(run_directory.resolve()),
        "model_load_seconds": model_load_seconds,
        "wall_seconds": time.perf_counter() - wall_started,
        "peak_rss_bytes": sampler.peak_rss_bytes,
        "mean_cpu_percent": sampler.mean_cpu_percent,
        "aggregate": aggregate,
        "files": file_results,
    }
    _write_json(report_path, report)
    summary_record = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config_hash": config_hash,
        "run_name": arguments.run_name,
        "split": arguments.split,
        "backend": arguments.backend,
        "model": arguments.model_id,
        "compute_type": arguments.compute_type,
        "beam_size": arguments.beam_size,
        "preprocessing": arguments.preprocessing,
        "pseudo_wer": aggregate["pseudo_wer"],
        "cer": aggregate["cer"],
        "rtf": aggregate["rtf"],
        "wall_rtf": aggregate["wall_rtf"],
        "peak_rss_bytes": sampler.peak_rss_bytes,
        "retries": aggregate["retries"],
        "cascade_retries": aggregate["cascade_retries"],
        "uncertain": aggregate["uncertain"],
        "run_directory": str(run_directory.resolve()),
    }
    _update_summary(arguments.output_dir, summary_record)
    print(f"complete {run_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
