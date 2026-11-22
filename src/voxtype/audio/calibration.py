"""Microphone calibration using the same normalized PCM contract as capture."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from .models import FloatAudio, mono_float32


class CalibrationStatus(StrEnum):
    GOOD = "good"
    QUIET_BUT_USABLE = "quiet_but_usable"
    TOO_QUIET = "too_quiet"
    NO_SPEECH_DETECTED = "no_speech_detected"
    TOO_NOISY = "too_noisy"
    CLIPPING = "clipping"
    DEVICE_ERROR = "device_error"


@dataclass(frozen=True, slots=True)
class PCMStatistics:
    minimum: float
    maximum: float
    mean: float
    rms: float
    peak: float
    p95_absolute: float
    dbfs: float


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    status: CalibrationStatus
    silence: PCMStatistics
    speech: PCMStatistics
    margin_db: float
    clipping_percentage: float
    speech_activity_ratio: float
    sample_rate: int
    channels: int
    input_dtype: str
    device_error: str = ""

    @property
    def usable(self) -> bool:
        return self.status in {
            CalibrationStatus.GOOD,
            CalibrationStatus.QUIET_BUT_USABLE,
        }

    @property
    def capture_worked(self) -> bool:
        return self.status is not CalibrationStatus.DEVICE_ERROR

    @property
    def title(self) -> str:
        return {
            CalibrationStatus.GOOD: "✓ Микрофон настроен",
            CalibrationStatus.QUIET_BUT_USABLE: "⚠ Голос немного тихий",
            CalibrationStatus.TOO_QUIET: "Речь почти не слышна",
            CalibrationStatus.NO_SPEECH_DETECTED: "Речь не обнаружена",
            CalibrationStatus.TOO_NOISY: "Слишком много фонового шума",
            CalibrationStatus.CLIPPING: "Сигнал перегружен",
            CalibrationStatus.DEVICE_ERROR: "Не удалось открыть микрофон",
        }[self.status]

    @property
    def message(self) -> str:
        return {
            CalibrationStatus.GOOD: "Уровень голоса хороший.",
            CalibrationStatus.QUIET_BUT_USABLE: (
                "Микрофон работает. Можно продолжить или повторить проверку."
            ),
            CalibrationStatus.TOO_QUIET: (
                "Говорите обычным голосом ближе к микрофону или выберите другое устройство."
            ),
            CalibrationStatus.NO_SPEECH_DETECTED: (
                "Во время второго этапа речь не была зарегистрирована."
            ),
            CalibrationStatus.TOO_NOISY: (
                "Голос слабо отличается от фонового шума. Попробуйте более тихое место."
            ),
            CalibrationStatus.CLIPPING: (
                "Микрофон перегружается. Отодвиньтесь немного дальше и повторите проверку."
            ),
            CalibrationStatus.DEVICE_ERROR: (
                f"Устройство недоступно ({self.device_error})."
                if self.device_error
                else "Устройство недоступно."
            ),
        }[self.status]

    @property
    def technical_details(self) -> str:
        noise = (
            (
                "тихий"
                if self.silence.dbfs < -55
                else "умеренный"
                if self.silence.dbfs < -38
                else "шумный"
            )
            if self.capture_worked
            else "не измерен"
        )
        return (
            f"dtype={self.input_dtype}; {self.sample_rate} Гц; channels={self.channels}; "
            f"noise RMS={self.silence.rms:.7f} ({self.silence.dbfs:.1f} dBFS); "
            f"speech RMS={self.speech.rms:.7f} ({self.speech.dbfs:.1f} dBFS); "
            f"peak={self.speech.peak:.7f} ({_to_dbfs(self.speech.peak):.1f} dBFS); "
            f"фон: {noise}; P95={self.speech.p95_absolute:.7f}; "
            f"margin={self.margin_db:.1f} dB; clipping={self.clipping_percentage:.3f}%; "
            f"speech activity={self.speech_activity_ratio * 100:.1f}%"
        )


def analyze_calibration(
    silence_samples: npt.ArrayLike,
    speech_samples: npt.ArrayLike,
    *,
    sample_rate: int,
    speech_activity_confirmed: bool | None = None,
    clipping_threshold: float = 0.985,
) -> CalibrationResult:
    """Classify a two-phase capture without requiring a loud absolute level.

    The absolute level separates ``GOOD`` from ``QUIET_BUT_USABLE``.  A quiet
    signal is accepted when it has confirmed activity and clear separation from
    the measured room floor.
    """

    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    raw_silence = np.asarray(silence_samples)
    raw_speech = np.asarray(speech_samples)
    channels = _channel_count(raw_silence, raw_speech)
    input_dtype = str(np.result_type(raw_silence.dtype, raw_speech.dtype))
    silence = mono_float32(raw_silence)
    speech = mono_float32(raw_speech)
    if silence.size == 0 or speech.size == 0:
        raise ValueError("calibration phases must not be empty")

    silence_stats = _statistics(silence)
    speech_stats = _statistics(speech)
    margin_db = speech_stats.dbfs - silence_stats.dbfs
    clipping = float(np.mean(np.abs(speech) >= clipping_threshold) * 100.0)
    activity_ratio = _speech_activity_ratio(speech, silence_stats.rms, sample_rate)
    speech_detected = (
        bool(speech_activity_confirmed)
        if speech_activity_confirmed is not None
        else activity_ratio >= 0.08
    )

    if clipping >= 0.5:
        status = CalibrationStatus.CLIPPING
    elif not speech_detected:
        status = CalibrationStatus.NO_SPEECH_DETECTED
    elif speech_stats.dbfs <= -78.0:
        status = CalibrationStatus.TOO_QUIET
    elif margin_db < 6.0:
        status = CalibrationStatus.TOO_NOISY
    elif speech_stats.dbfs < -42.0:
        status = CalibrationStatus.QUIET_BUT_USABLE
    else:
        status = CalibrationStatus.GOOD

    return CalibrationResult(
        status=status,
        silence=silence_stats,
        speech=speech_stats,
        margin_db=margin_db,
        clipping_percentage=clipping,
        speech_activity_ratio=activity_ratio,
        sample_rate=sample_rate,
        channels=channels,
        input_dtype=input_dtype,
    )


def device_error_result(
    message: str,
    *,
    sample_rate: int = 0,
    channels: int = 1,
    input_dtype: str = "float32",
) -> CalibrationResult:
    empty = PCMStatistics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -120.0)
    return CalibrationResult(
        status=CalibrationStatus.DEVICE_ERROR,
        silence=empty,
        speech=empty,
        margin_db=0.0,
        clipping_percentage=0.0,
        speech_activity_ratio=0.0,
        sample_rate=sample_rate,
        channels=channels,
        input_dtype=input_dtype,
        device_error=message,
    )


def _statistics(audio: FloatAudio) -> PCMStatistics:
    absolute = np.abs(audio)
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return PCMStatistics(
        minimum=float(np.min(audio)),
        maximum=float(np.max(audio)),
        mean=float(np.mean(audio, dtype=np.float64)),
        rms=rms,
        peak=float(np.max(absolute)),
        p95_absolute=float(np.percentile(absolute, 95)),
        dbfs=_to_dbfs(rms),
    )


def _speech_activity_ratio(audio: FloatAudio, noise_rms: float, sample_rate: int) -> float:
    frame_size = max(1, round(sample_rate * 0.03))
    usable = audio[: audio.size - audio.size % frame_size]
    if usable.size == 0:
        usable = audio
        frame_size = audio.size
    frames = usable.reshape(-1, frame_size)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    threshold = max(noise_rms * 2.0, 10.0 ** (-85.0 / 20.0))
    return float(np.mean(rms >= threshold))


def _channel_count(*arrays: npt.NDArray[np.generic]) -> int:
    counts = [int(array.shape[1]) for array in arrays if array.ndim == 2]
    return max(counts, default=1)


def _to_dbfs(value: float) -> float:
    return max(-120.0, 20.0 * math.log10(max(value, 1e-12)))
