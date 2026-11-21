"""Lazy, strictly local Silero VAD adapter with an energy fallback."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np
import numpy.typing as npt

from voxtype.audio.models import mono_float32
from voxtype.core.compute_budget import ComputeBudget

from .base import AdaptiveEnergyVAD, VoiceActivityDetector


class VADUnavailable(RuntimeError):
    """Silero was requested strictly but its local runtime cannot be loaded."""


_onnx_load_lock = RLock()


@lru_cache(maxsize=1)
def _load_onnx_session(path: Path, modified_ns: int, size: int) -> Any:
    # File metadata invalidates the cached session if the model is replaced.
    import onnxruntime as ort

    budget = ComputeBudget(0)
    options = ort.SessionOptions()
    options.intra_op_num_threads = budget.vad_intra_threads
    options.inter_op_num_threads = budget.vad_inter_threads
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )


class SileroVAD:
    """Load a local ONNX (preferred) or TorchScript model on first use.

    The pinned ONNX Runtime path needs no PyTorch installation.  No model hub is
    called, so recognizing speech can never trigger a hidden network request.
    Non-strict mode degrades to the adaptive energy VAD and exposes that choice
    through ``runtime`` and ``using_fallback``.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        strict: bool = True,
        fallback: VoiceActivityDetector | None = None,
        model: Any = None,
        onnx_session: Any = None,
    ) -> None:
        self.model_path = Path(model_path).resolve() if model_path is not None else None
        self.strict = strict
        self.fallback = fallback or AdaptiveEnergyVAD()
        self._model = model
        self._onnx_session = onnx_session
        self._onnx_input_names: tuple[str, ...] | None = None
        self._load_attempted = model is not None or onnx_session is not None
        self._load_error: BaseException | None = None
        self._state: npt.NDArray[np.float32] = np.zeros((2, 1, 128), dtype=np.float32)
        self._context: npt.NDArray[np.float32] = np.empty((1, 0), dtype=np.float32)
        self._last_sample_rate = 0
        self._lock = RLock()

    @property
    def using_fallback(self) -> bool:
        return self._load_attempted and self._model is None and self._onnx_session is None

    @property
    def runtime(self) -> str:
        if self._onnx_session is not None:
            return "onnxruntime"
        if self._model is not None:
            return "torchscript"
        if self._load_attempted:
            return "energy-fallback"
        return "not-loaded"

    @property
    def load_error(self) -> BaseException | None:
        return self._load_error

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._load_attempted:
                if self.strict and self.using_fallback:
                    raise VADUnavailable(
                        "Не удалось загрузить локальную модель Silero VAD."
                    ) from self._load_error
                return
            self._load_attempted = True
            try:
                if self.model_path is None or not self.model_path.is_file():
                    raise FileNotFoundError("local Silero model was not found")
                if self.model_path.suffix.casefold() == ".onnx":
                    with _onnx_load_lock:
                        stat = self.model_path.stat()
                        self._onnx_session = _load_onnx_session(
                            self.model_path, stat.st_mtime_ns, stat.st_size
                        )
                else:
                    import torch

                    self._model = torch.jit.load(str(self.model_path), map_location="cpu")
                    self._model.eval()
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self._load_error = exc
                self._model = None
                if self.strict:
                    raise VADUnavailable(
                        "Не удалось загрузить локальную модель Silero VAD."
                    ) from exc

    def speech_probability(self, samples: npt.ArrayLike, sample_rate: int) -> float:
        audio = mono_float32(samples)
        if audio.size == 0:
            return 0.0
        self._ensure_loaded()
        model = self._model
        session = self._onnx_session
        if model is None and session is None:
            return self.fallback.speech_probability(audio, sample_rate)
        if sample_rate not in (8_000, 16_000):
            if self.strict:
                raise ValueError("Silero VAD accepts 8 kHz or 16 kHz PCM")
            return self.fallback.speech_probability(audio, sample_rate)
        expected = 512 if sample_rate == 16_000 else 256
        if audio.size < expected:
            inference_audio = np.pad(audio, (0, expected - audio.size))
        else:
            inference_audio = audio[-expected:]
        if session is not None:
            return self._onnx_probability(session, inference_audio, sample_rate, audio)
        try:
            import torch

            if model is None:
                raise RuntimeError("TorchScript model disappeared during inference")
            with torch.inference_mode():
                output = model(torch.from_numpy(inference_audio), sample_rate)
            probability = float(output.item() if hasattr(output, "item") else output)
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            if self.strict:
                raise VADUnavailable("Silero VAD inference failed") from exc
            self._load_error = exc
            self._model = None
            return self.fallback.speech_probability(audio, sample_rate)
        return min(1.0, max(0.0, probability))

    def _onnx_probability(
        self,
        session: Any,
        inference_audio: np.ndarray[Any, np.dtype[np.float32]],
        sample_rate: int,
        original_audio: np.ndarray[Any, np.dtype[np.float32]],
    ) -> float:
        try:
            context_size = 64 if sample_rate == 16_000 else 32
            if self._last_sample_rate and self._last_sample_rate != sample_rate:
                self._state.fill(0.0)
                self._context = np.empty((1, 0), dtype=np.float32)
            if self._context.shape != (1, context_size):
                self._context = np.zeros((1, context_size), dtype=np.float32)
            model_audio = np.concatenate(
                (self._context, inference_audio.reshape(1, -1)), axis=1
            ).astype(np.float32, copy=False)
            if self._onnx_input_names is None:
                self._onnx_input_names = tuple(item.name for item in session.get_inputs())
            feeds: dict[str, np.ndarray[Any, Any]] = {}
            for name in self._onnx_input_names:
                normalized = name.casefold()
                if normalized in {"input", "audio", "x"}:
                    feeds[name] = model_audio
                elif normalized in {"state", "state_1"}:
                    feeds[name] = self._state
                elif normalized in {"sr", "sample_rate"}:
                    feeds[name] = np.array(sample_rate, dtype=np.int64)
                else:
                    raise ValueError(f"unsupported Silero ONNX input: {name}")
            outputs = session.run(None, feeds)
            if not outputs:
                raise RuntimeError("Silero ONNX returned no outputs")
            probability = float(np.asarray(outputs[0]).reshape(-1)[0])
            if len(outputs) > 1:
                next_state = np.asarray(outputs[1], dtype=np.float32)
                if next_state.shape != self._state.shape:
                    raise ValueError("Silero ONNX returned an invalid recurrent state")
                self._state = next_state.copy()
            self._context = model_audio[:, -context_size:].copy()
            self._last_sample_rate = sample_rate
        except (RuntimeError, TypeError, ValueError, IndexError) as exc:
            if self.strict:
                raise VADUnavailable("Silero ONNX inference failed") from exc
            self._load_error = exc
            self._onnx_session = None
            self._state.fill(0.0)
            self._context = np.empty((1, 0), dtype=np.float32)
            self._last_sample_rate = 0
            return self.fallback.speech_probability(original_audio, sample_rate)
        return min(1.0, max(0.0, probability))

    @staticmethod
    def release_cached_session() -> None:
        with _onnx_load_lock:
            _load_onnx_session.cache_clear()

    def reset(self) -> None:
        with self._lock:
            model = self._model
            if model is not None and hasattr(model, "reset_states"):
                model.reset_states()
            self._state.fill(0.0)
            self._context = np.empty((1, 0), dtype=np.float32)
            self._last_sample_rate = 0
        self.fallback.reset()
