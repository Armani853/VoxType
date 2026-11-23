from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from voxtype.vad.base import AdaptiveEnergyVAD
from voxtype.vad.segmenter import (
    SegmentationSettings,
    SegmentReason,
    StreamingSegmenter,
)
from voxtype.vad.silero import SileroVAD, VADUnavailable


class AmplitudeVAD:
    def speech_probability(self, samples: np.ndarray, sample_rate: int) -> float:
        del sample_rate
        return 0.95 if np.max(np.abs(samples), initial=0.0) >= 0.2 else 0.02

    def reset(self) -> None:
        return None


def _feed(segmenter: StreamingSegmenter, audio: np.ndarray, block: int = 137) -> list:
    segments = []
    for start in range(0, audio.size, block):
        segments.extend(segmenter.process(audio[start : start + block]))
    segments.extend(segmenter.flush())
    return segments


def test_natural_segment_preserves_pre_and_post_roll() -> None:
    segmenter = StreamingSegmenter(AmplitudeVAD(), sample_rate=1_000)
    audio = np.r_[np.zeros(500), np.full(1_000, 0.5), np.zeros(800)].astype(np.float32)
    segments = _feed(segmenter, audio)
    assert len(segments) == 1
    segment = segments[0]
    assert segment.reason is SegmentReason.SILENCE
    assert 50 <= segment.start_sample <= 150
    assert 1_780 <= segment.end_sample <= 1_830
    assert segment.speech_seconds >= 0.95


def test_short_pause_does_not_end_speech() -> None:
    segmenter = StreamingSegmenter(AmplitudeVAD(), sample_rate=1_000)
    audio = np.r_[
        np.zeros(400),
        np.full(800, 0.5),
        np.zeros(300),
        np.full(700, 0.5),
        np.zeros(800),
    ].astype(np.float32)
    segments = _feed(segmenter, audio, 91)
    assert len(segments) == 1
    assert segments[0].reason is SegmentReason.SILENCE
    assert segments[0].end_sample >= 2_480


def test_conversational_vad_keeps_weak_speech_after_an_onset() -> None:
    class LevelProbabilityVAD(AmplitudeVAD):
        def speech_probability(self, samples: np.ndarray, sample_rate: int) -> float:
            return float(np.max(samples, initial=0.0))

    segmenter = StreamingSegmenter(
        LevelProbabilityVAD(), sample_rate=1_000, settings=SegmentationSettings.conversational(0.5)
    )
    audio = np.repeat(np.array([0.35, 0.7, 0.35, 0.0], dtype=np.float32), [640, 640, 640, 800])
    segments = _feed(segmenter, audio)
    assert len(segments) == 1
    assert segments[0].speech_seconds == pytest.approx(1.28)
    assert segments[0].end_sample == 2320


def test_forced_segmentation_limits_unbroken_speech_and_carries_overlap() -> None:
    settings = SegmentationSettings(
        preferred_segment_seconds=12.0,
        maximum_segment_seconds=18.0,
        overlap_seconds=1.2,
    )
    segmenter = StreamingSegmenter(AmplitudeVAD(), sample_rate=1_000, settings=settings)
    audio = np.r_[np.zeros(500), np.full(40_000, 0.5)].astype(np.float32)
    segments = _feed(segmenter, audio, 73)
    assert len(segments) >= 3
    assert all(segment.duration_seconds <= 18.001 for segment in segments[:-1])
    assert all(segment.reason is SegmentReason.MAXIMUM_DURATION for segment in segments[:-1])
    for previous, current in pairwise(segments):
        assert previous.end_sample - current.start_sample == 1_200
        assert current.leading_overlap_seconds == pytest.approx(1.2)


def test_forced_segmentation_prefers_short_pause_after_target() -> None:
    settings = SegmentationSettings(
        preferred_segment_seconds=12.0,
        maximum_segment_seconds=18.0,
        overlap_seconds=1.2,
    )
    segmenter = StreamingSegmenter(AmplitudeVAD(), sample_rate=1_000, settings=settings)
    audio = np.r_[
        np.zeros(400),
        np.full(12_200, 0.5),
        np.zeros(220),
        np.full(2_000, 0.5),
        np.zeros(800),
    ].astype(np.float32)
    segments = _feed(segmenter, audio, 83)
    assert len(segments) == 2
    assert segments[0].reason is SegmentReason.PREFERRED_PAUSE
    assert 12.0 <= segments[0].duration_seconds <= 13.0
    assert segments[1].leading_overlap_seconds == pytest.approx(1.2)


def test_default_segmentation_uses_selected_accuracy_configuration() -> None:
    settings = SegmentationSettings()

    assert settings.preferred_segment_seconds == 15.0
    assert settings.maximum_segment_seconds == 25.0
    assert settings.overlap_seconds == 0.8


def test_subminimum_burst_is_not_sent_to_asr() -> None:
    segmenter = StreamingSegmenter(AmplitudeVAD(), sample_rate=1_000)
    audio = np.r_[np.zeros(500), np.full(100, 0.5), np.zeros(900)].astype(np.float32)
    assert _feed(segmenter, audio) == []


def test_flush_commits_active_tail_without_waiting_for_silence() -> None:
    segmenter = StreamingSegmenter(AmplitudeVAD(), sample_rate=1_000)
    audio = np.r_[np.zeros(400), np.full(900, 0.5)].astype(np.float32)
    segments = _feed(segmenter, audio)
    assert len(segments) == 1
    assert segments[0].reason is SegmentReason.FLUSH
    assert segments[0].end_sample == audio.size


def test_energy_vad_adapts_and_resets() -> None:
    detector = AdaptiveEnergyVAD()
    quiet = np.zeros(480, dtype=np.float32)
    speech = (0.2 * np.sin(2 * np.pi * 200 * np.arange(480) / 16_000)).astype(np.float32)
    assert detector.speech_probability(quiet, 16_000) < 0.1
    assert detector.speech_probability(speech, 16_000) > 0.8
    detector.reset()
    assert detector.noise_floor_dbfs == pytest.approx(-55.0)


def test_silero_is_lazy_and_falls_back_without_torch_or_model(tmp_path: Path) -> None:
    detector = SileroVAD(tmp_path / "missing.jit", strict=False)
    assert not detector.using_fallback
    probability = detector.speech_probability(np.full(512, 0.3, dtype=np.float32), 16_000)
    assert detector.using_fallback
    assert probability > 0.5

    strict = SileroVAD(tmp_path / "missing.jit", strict=True)
    with pytest.raises(VADUnavailable):
        strict.speech_probability(np.ones(512, dtype=np.float32), 16_000)


def test_silero_onnx_path_uses_context_and_recurrent_state() -> None:
    class Input:
        def __init__(self, name: str) -> None:
            self.name = name

    class Session:
        def __init__(self) -> None:
            self.states: list[np.ndarray] = []
            self.audio_inputs: list[np.ndarray] = []

        def get_inputs(self) -> list[Input]:
            return [Input("input"), Input("state"), Input("sr")]

        def run(self, outputs: object, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
            assert outputs is None
            self.audio_inputs.append(feeds["input"].copy())
            self.states.append(feeds["state"].copy())
            assert int(feeds["sr"]) == 16_000
            return [np.array([[0.87]], dtype=np.float32), feeds["state"] + 1.0]

    session = Session()
    detector = SileroVAD(strict=True, onnx_session=session)
    assert detector.runtime == "onnxruntime"
    assert detector.speech_probability(np.ones(480, dtype=np.float32), 16_000) == pytest.approx(
        0.87
    )
    assert detector.speech_probability(np.ones(700, dtype=np.float32), 16_000) == pytest.approx(
        0.87
    )
    assert [item.shape for item in session.audio_inputs] == [(1, 576), (1, 576)]
    assert np.all(session.audio_inputs[0][:, :64] == 0.0)
    assert np.array_equal(session.audio_inputs[1][:, :64], session.audio_inputs[0][:, -64:])
    assert np.all(session.states[0] == 0.0)
    assert np.all(session.states[1] == 1.0)
    detector.reset()
    detector.speech_probability(np.ones(512, dtype=np.float32), 16_000)
    assert np.all(session.states[2] == 0.0)
    assert np.all(session.audio_inputs[2][:, :64] == 0.0)


def test_segmentation_settings_enforce_required_padding() -> None:
    with pytest.raises(ValueError, match="pre-roll"):
        SegmentationSettings(pre_roll_ms=100)


def test_segmenter_reset_can_preserve_monotonic_transcript_sequence() -> None:
    segmenter = StreamingSegmenter(AmplitudeVAD())
    speech = np.full(16_000, 0.5, dtype=np.float32)
    first = segmenter.process(speech) or segmenter.flush()
    assert first
    segmenter.reset(preserve_sequence=True)
    second = segmenter.process(speech) or segmenter.flush()
    assert second[0].sequence > first[-1].sequence
