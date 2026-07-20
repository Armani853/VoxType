from __future__ import annotations

import hashlib
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voxtype.asr.base import (
    ASRBackend,
    ASRModelUnavailable,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
    bounded_prompt,
)
from voxtype.asr.benchmark import BenchmarkCase, BenchmarkResult, benchmark_backend, select_profile
from voxtype.asr.faster_whisper_backend import FasterWhisperBackend
from voxtype.asr.hallucination_guard import HallucinationGuard, HallucinationIssue
from voxtype.asr.model_manager import ModelManager, ModelManagerError, ModelSpec
from voxtype.asr.profiles import ASRProfile, get_profile


def _request(*, language: str = "ru", silence: bool = False) -> TranscriptionRequest:
    audio = (
        np.zeros(16_000, dtype=np.float32) if silence else np.full(16_000, 0.08, dtype=np.float32)
    )
    return TranscriptionRequest(audio, language=language)  # type: ignore[arg-type]


def _result(
    text: str,
    *,
    language: str = "ru",
    probability: float = 0.9,
    compression: float = 1.0,
) -> TranscriptionResult:
    words = tuple(
        WordTimestamp(
            word=f" {word}", start=index * 0.1, end=(index + 1) * 0.1, probability=probability
        )
        for index, word in enumerate(text.split())
    )
    segment = TranscriptionSegment(
        text=text,
        start=0.0,
        end=1.0,
        words=words,
        average_log_probability=-0.1,
        no_speech_probability=0.01,
        compression_ratio=compression,
    )
    return TranscriptionResult(
        text=text,
        language=language,  # type: ignore[arg-type]
        segments=(segment,),
        audio_duration_seconds=1.0,
        processing_seconds=0.1,
        model_id="fake",
        backend_name="fake",
    )


class FakeBackend:
    def __init__(self, results: list[TranscriptionResult], model_id: str = "fake") -> None:
        self.results = results
        self._model_id = model_id
        self.calls: list[TranscriptionRequest] = []
        self._loaded = False

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        self._loaded = True

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.calls.append(request)
        return self.results.pop(0)

    def unload(self) -> None:
        self._loaded = False


def test_request_requires_explicit_language_and_bounds_context() -> None:
    with pytest.raises(ValueError, match="explicitly"):
        TranscriptionRequest(np.zeros(100, dtype=np.float32), language="auto")  # type: ignore[arg-type]
    prompt = bounded_prompt(
        "слово " * 300, ("VoxType", "CTranslate2", "VoxType"), max_characters=180
    )
    assert len(prompt) <= 180
    assert "VoxType" in prompt
    retry = replace(
        _request(), initial_prompt="bad context", condition_on_previous_text=True
    ).conservative_retry()
    assert retry.initial_prompt == ""
    assert not retry.condition_on_previous_text
    assert retry.beam_size == 5


def test_faster_whisper_backend_loads_lazily_and_maps_word_timestamps(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    calls: dict[str, Any] = {}

    class Engine:
        def transcribe(self, audio: np.ndarray, **options: Any) -> tuple[Any, Any]:
            calls["audio"] = audio
            calls["options"] = options
            word = SimpleNamespace(word=" тест", start=0.1, end=0.4, probability=0.93)
            segment = SimpleNamespace(
                text=" тест",
                start=0.0,
                end=0.5,
                words=[word],
                avg_logprob=-0.2,
                no_speech_prob=0.01,
                compression_ratio=1.1,
            )
            return iter([segment]), SimpleNamespace(language="ru")

    def factory(model: str, **options: Any) -> Engine:
        calls["model"] = model
        calls["load_options"] = options
        return Engine()

    backend = FasterWhisperBackend(
        model_path, get_profile(ASRProfile.BALANCED), model_factory=factory
    )
    assert not backend.loaded
    result = backend.transcribe(
        replace(_request(), start_offset_seconds=10.0, initial_prompt="VoxType", beam_size=2)
    )
    assert backend.loaded
    assert result.text == "тест"
    assert result.words[0].start == pytest.approx(10.1)
    assert calls["options"]["language"] == "ru"
    assert calls["options"]["beam_size"] == 2
    assert calls["options"]["vad_filter"] is False
    assert calls["load_options"]["local_files_only"] is True
    backend.unload()
    assert not backend.loaded


def test_faster_whisper_refuses_implicit_download(tmp_path: Path) -> None:
    backend = FasterWhisperBackend(tmp_path / "missing", get_profile(ASRProfile.FAST))
    with pytest.raises(ASRModelUnavailable, match="не установлена"):
        backend.load()


def test_hallucination_guard_detects_silence_repetition_junk_and_language() -> None:
    guard = HallucinationGuard()
    repeated = "тест тест тест тест тест тест тест тест тест тест"
    assessment = guard.assess(
        _request(silence=True),
        _result(f"{repeated}. {repeated}. {repeated}.", probability=0.1, compression=3.2),
    )
    assert assessment.suspicious
    assert HallucinationIssue.TEXT_ON_SILENCE in assessment.issues
    assert HallucinationIssue.REPEATED_WORD in assessment.issues
    assert HallucinationIssue.HIGH_COMPRESSION in assessment.issues

    junk = guard.assess(_request(), _result("Спасибо за просмотр."))
    assert HallucinationIssue.SUBTITLE_JUNK in junk.issues
    wrong_language = guard.assess(
        _request(language="ru"), _result("this text is entirely in English language")
    )
    assert HallucinationIssue.WRONG_LANGUAGE in wrong_language.issues


def test_hallucination_guard_flags_committed_duplicate() -> None:
    guard = HallucinationGuard()
    result = _result("это уже подтвержденный текст")
    assessment = guard.assess(
        _request(),
        result,
        committed_text="Начало. Это уже подтвержденный текст",
    )
    assert HallucinationIssue.DUPLICATE_COMMITTED_TEXT in assessment.issues


def test_guard_retries_once_and_selects_more_plausible_result() -> None:
    suspicious = _result("слово слово слово слово слово слово слово слово слово")
    clean = _result("это нормальный результат распознавания")
    backend = FakeBackend([suspicious, clean])
    guarded = HallucinationGuard().transcribe_guarded(backend, _request())
    assert guarded.retried
    assert len(backend.calls) == 2
    assert backend.calls[1].initial_prompt == ""
    assert guarded.result.text == clean.text
    assert guarded.result.retry_count == 1
    assert not guarded.result.possible_inaccuracy


def test_guard_keeps_suspicious_text_with_warning_if_retry_fails() -> None:
    class FailingBackend(FakeBackend):
        def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
            self.calls.append(request)
            if len(self.calls) == 2:
                raise RuntimeError("worker stopped")
            return _result("повтор повтор повтор повтор повтор повтор повтор повтор")

    backend = FailingBackend([])
    guarded = HallucinationGuard().transcribe_guarded(backend, _request())
    assert guarded.result.text
    assert guarded.result.possible_inaccuracy
    assert HallucinationIssue.RETRY_FAILED in guarded.assessment.issues
    assert len(backend.calls) == 2


def test_benchmark_and_profile_selection_use_measured_maximum_rtf() -> None:
    result = _result("ok")
    backend: ASRBackend = FakeBackend([result])
    clock_values = iter((0.0, 0.4))
    measured = benchmark_backend(
        ASRProfile.FAST,
        backend,
        [BenchmarkCase(_request())],
        clock=lambda: next(clock_values),
    )
    assert measured.successful
    assert measured.maximum_rtf == pytest.approx(0.4)

    results = [
        measured,
        BenchmarkResult(ASRProfile.BALANCED, "balanced", True, 0.5, 0.65, 0.5, 10, 5, 2),
        BenchmarkResult(ASRProfile.MAXIMUM_ACCURACY, "large", True, 0.8, 0.9, 0.8, 10, 8, 2),
    ]
    assert select_profile(results, target_rtf=0.7) is ASRProfile.BALANCED
    assert select_profile(results, target_rtf=0.3) is ASRProfile.FAST


def test_model_manager_registers_verifies_detects_tamper_and_removes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "model.bin").write_bytes(b"local model weights")
    manager = ModelManager(tmp_path / "models")
    spec = ModelSpec("test-model", repository_id="local/example")
    installed = manager.register_local(spec, source)
    assert manager.is_installed("test-model", verify=True)
    assert manager.list_installed(verify=True)[0].verified
    (installed / "model.bin").write_bytes(b"tampered")
    assert not manager.verify("test-model")
    assert manager.remove("test-model")
    assert not installed.exists()


def test_model_manager_installs_checked_zip_atomically(tmp_path: Path) -> None:
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("converted/config.json", "{}")
        package.writestr("converted/model.bin", b"weights")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    spec = ModelSpec(
        "archive-model",
        archive_url=archive.as_uri(),
        archive_sha256=digest,
        expected_size_bytes=archive.stat().st_size,
    )
    manager = ModelManager(tmp_path / "models")
    progress: list[tuple[int, int | None]] = []
    installed = manager.install(
        spec, progress=lambda current, total: progress.append((current, total))
    )
    assert installed.name == "archive-model"
    assert manager.verify("archive-model")
    assert progress[0][0] == 0
    assert progress[-1][0] > 0
    assert not tuple((tmp_path / "models").glob("*.staging"))


def test_model_manager_rejects_bad_checksum_without_publishing(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("config.json", "{}")
        package.writestr("model.bin", b"weights")
    spec = ModelSpec(
        "bad-model",
        archive_url=archive.as_uri(),
        archive_sha256="0" * 64,
        expected_size_bytes=archive.stat().st_size,
    )
    manager = ModelManager(tmp_path / "models")
    with pytest.raises(ModelManagerError, match="сумма"):
        manager.install(spec)
    assert not manager.path_for("bad-model").exists()


def test_backlog_profile_only_reduces_optional_search() -> None:
    balanced = get_profile(ASRProfile.BALANCED)
    backlog = balanced.for_backlog()
    assert backlog.model_name == balanced.model_name
    assert backlog.compute_type == balanced.compute_type
    assert backlog.beam_size == 1
    assert backlog.best_of == 1
