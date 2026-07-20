"""Lazy faster-whisper/CTranslate2 implementation of the ASR protocol."""

from __future__ import annotations

import gc
import zlib
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any

from .base import (
    ASRDependencyUnavailable,
    ASRModelUnavailable,
    LanguageCode,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from .profiles import ASRProfileConfig


class FasterWhisperBackend:
    """Keep heavy imports and weights outside application startup.

    By default ``model`` must resolve to an installed local directory.  Setting
    ``allow_download`` is an explicit opt-in intended for ModelManager-driven
    setup, never for ordinary offline dictation.
    """

    def __init__(
        self,
        model: str | Path,
        profile: ASRProfileConfig,
        *,
        device: str = "cpu",
        device_index: int | list[int] = 0,
        allow_download: bool = False,
        download_root: str | Path | None = None,
        model_factory: Any = None,
    ) -> None:
        self.model = str(model)
        self.profile = profile
        self.device = device
        self.device_index = device_index
        self.allow_download = allow_download
        self.download_root = str(download_root) if download_root is not None else None
        self._model_factory = model_factory
        self._engine: Any = None
        self._lock = RLock()

    @property
    def model_id(self) -> str:
        return self.profile.model_name

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._engine is not None

    def load(self) -> None:
        with self._lock:
            if self._engine is not None:
                return
            model_path = Path(self.model)
            if not self.allow_download and not model_path.is_dir():
                raise ASRModelUnavailable(
                    f"Локальная модель '{self.model_id}' не установлена: {model_path}"
                )
            factory = self._model_factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel
                except (ImportError, OSError) as exc:
                    raise ASRDependencyUnavailable(
                        "Для распознавания установите совместимые faster-whisper и CTranslate2."
                    ) from exc
                factory = WhisperModel
            try:
                self._engine = factory(
                    self.model,
                    device=self.device,
                    device_index=self.device_index,
                    compute_type=self.profile.compute_type,
                    cpu_threads=self.profile.cpu_threads,
                    num_workers=1,
                    download_root=self.download_root,
                    local_files_only=not self.allow_download,
                )
            except (RuntimeError, ValueError, OSError) as exc:
                self._engine = None
                raise ASRModelUnavailable(
                    f"Не удалось загрузить модель '{self.model_id}'."
                ) from exc

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        if request.sample_rate != 16_000:
            raise ValueError("faster-whisper backend requires 16 kHz PCM")
        self.load()
        with self._lock:
            engine = self._engine
            if engine is None:
                raise ASRModelUnavailable(f"model '{self.model_id}' is not loaded")
            options: dict[str, Any] = {
                "language": request.language,
                "task": "transcribe",
                "temperature": request.temperature,
                "beam_size": request.beam_size or self.profile.beam_size,
                "best_of": self.profile.best_of,
                "word_timestamps": self.profile.word_timestamps,
                "condition_on_previous_text": request.condition_on_previous_text,
                "initial_prompt": request.initial_prompt[:600] or None,
                "vad_filter": False,
                "no_speech_threshold": request.no_speech_threshold
                if request.no_speech_threshold is not None
                else self.profile.no_speech_threshold,
                "log_prob_threshold": request.log_probability_threshold
                if request.log_probability_threshold is not None
                else self.profile.log_probability_threshold,
                "compression_ratio_threshold": request.compression_ratio_threshold
                if request.compression_ratio_threshold is not None
                else self.profile.compression_ratio_threshold,
            }
            started = perf_counter()
            try:
                raw_segments, info = engine.transcribe(request.audio, **options)
                segments = tuple(
                    self._convert_segment(item, request.start_offset_seconds)
                    for item in raw_segments
                )
            except (RuntimeError, ValueError, OSError) as exc:
                raise ASRModelUnavailable(f"Inference failed for model '{self.model_id}'.") from exc
            elapsed = perf_counter() - started
        text = "".join(segment.text for segment in segments).strip()
        del info
        language: LanguageCode = request.language
        return TranscriptionResult(
            text=text,
            language=language,
            segments=segments,
            audio_duration_seconds=request.duration_seconds,
            processing_seconds=elapsed,
            model_id=self.model_id,
            backend_name="faster-whisper",
        )

    @staticmethod
    def _convert_segment(raw: Any, offset: float) -> TranscriptionSegment:
        text = str(getattr(raw, "text", ""))
        words: list[WordTimestamp] = []
        for item in getattr(raw, "words", None) or ():
            probability = getattr(item, "probability", None)
            words.append(
                WordTimestamp(
                    word=str(getattr(item, "word", "")),
                    start=max(0.0, float(getattr(item, "start", 0.0)) + offset),
                    end=max(0.0, float(getattr(item, "end", 0.0)) + offset),
                    probability=float(probability) if probability is not None else None,
                )
            )
        compression = getattr(raw, "compression_ratio", None)
        if compression is None:
            encoded = text.encode("utf-8")
            compression = len(encoded) / max(len(zlib.compress(encoded)), 1) if encoded else 0.0
        return TranscriptionSegment(
            text=text,
            start=max(0.0, float(getattr(raw, "start", 0.0)) + offset),
            end=max(0.0, float(getattr(raw, "end", 0.0)) + offset),
            words=tuple(words),
            average_log_probability=_optional_float(getattr(raw, "avg_logprob", None)),
            no_speech_probability=_optional_float(getattr(raw, "no_speech_prob", None)),
            compression_ratio=_optional_float(compression),
        )

    def unload(self) -> None:
        with self._lock:
            self._engine = None
        gc.collect()

    def __enter__(self) -> FasterWhisperBackend:
        self.load()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.unload()


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
