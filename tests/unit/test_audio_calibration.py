import numpy as np
import pytest

from voxtype.audio.calibration import (
    CalibrationStatus,
    analyze_calibration,
    device_error_result,
)
from voxtype.ui.widgets import rms_to_meter


def _tone(rms: float, frames: int = 16_000, sample_rate: int = 16_000) -> np.ndarray:
    time = np.arange(frames, dtype=np.float64) / sample_rate
    return (rms * np.sqrt(2.0) * np.sin(2.0 * np.pi * 220.0 * time)).astype(np.float32)


def _noise(rms: float, frames: int = 16_000) -> np.ndarray:
    random = np.random.default_rng(2026).normal(size=frames)
    random *= rms / np.sqrt(np.mean(random**2))
    return random.astype(np.float32)


def test_calibration_classifies_normal_signal() -> None:
    result = analyze_calibration(
        _noise(10 ** (-62 / 20)),
        _tone(10 ** (-24 / 20)),
        sample_rate=16_000,
    )
    assert result.status is CalibrationStatus.GOOD
    assert result.usable


def test_quiet_but_distinct_realtek_regression_is_usable() -> None:
    result = analyze_calibration(
        _noise(10 ** (-80.7 / 20)),
        _tone(10 ** (-64.4 / 20)),
        sample_rate=16_000,
        speech_activity_confirmed=True,
    )
    assert result.silence.dbfs == pytest.approx(-80.7, abs=0.1)
    assert result.speech.dbfs == pytest.approx(-64.4, abs=0.1)
    assert result.margin_db == pytest.approx(16.3, abs=0.2)
    assert result.clipping_percentage == 0.0
    assert result.status is CalibrationStatus.QUIET_BUT_USABLE
    assert result.usable


def test_calibration_classifies_too_quiet_confirmed_activity() -> None:
    result = analyze_calibration(
        _noise(10 ** (-105 / 20)),
        _tone(10 ** (-82 / 20)),
        sample_rate=16_000,
        speech_activity_confirmed=True,
    )
    assert result.status is CalibrationStatus.TOO_QUIET
    assert not result.usable


def test_calibration_classifies_silence_only() -> None:
    silence = _noise(10 ** (-75 / 20))
    result = analyze_calibration(silence, silence.copy(), sample_rate=16_000)
    assert result.status is CalibrationStatus.NO_SPEECH_DETECTED


def test_calibration_classifies_high_noise_when_speech_activity_exists() -> None:
    result = analyze_calibration(
        _noise(10 ** (-28 / 20)),
        _noise(10 ** (-25 / 20)) + _tone(10 ** (-29 / 20)),
        sample_rate=16_000,
        speech_activity_confirmed=True,
    )
    assert result.margin_db < 6.0
    assert result.status is CalibrationStatus.TOO_NOISY


def test_calibration_classifies_real_clipping() -> None:
    speech = _tone(0.2)
    speech[:200] = 1.0
    result = analyze_calibration(
        _noise(0.001),
        speech,
        sample_rate=16_000,
        speech_activity_confirmed=True,
    )
    assert result.clipping_percentage > 0.5
    assert result.status is CalibrationStatus.CLIPPING


def test_float32_and_int16_pcm_have_the_same_scale() -> None:
    silence = _noise(0.001)
    speech = _tone(0.05)
    float_result = analyze_calibration(silence, speech, sample_rate=16_000)
    int_result = analyze_calibration(
        np.round(silence * 32767).astype(np.int16),
        np.round(speech * 32767).astype(np.int16),
        sample_rate=16_000,
    )
    assert float_result.speech.rms == pytest.approx(int_result.speech.rms, rel=0.002)
    assert int_result.input_dtype == "int16"
    assert int_result.status is float_result.status


def test_multichannel_pcm_is_converted_once_to_mono() -> None:
    silence = np.column_stack((_noise(0.001), _noise(0.001)))
    mono = _tone(0.05)
    speech = np.column_stack((mono, mono))
    result = analyze_calibration(silence, speech, sample_rate=16_000)
    assert result.channels == 2
    assert result.speech.rms == pytest.approx(0.05, rel=0.01)
    assert result.status is CalibrationStatus.GOOD


def test_dbfs_meter_keeps_quiet_usable_voice_visible() -> None:
    quiet_rms = 10 ** (-64.4 / 20)
    assert rms_to_meter(0.0) == 0.0
    assert rms_to_meter(quiet_rms) > 0.1
    assert rms_to_meter(1.0) == 1.0


def test_device_error_is_explicit_and_cannot_be_overridden() -> None:
    result = device_error_result("PortAudioError", sample_rate=48_000)
    assert result.status is CalibrationStatus.DEVICE_ERROR
    assert not result.capture_worked
    assert not result.usable
