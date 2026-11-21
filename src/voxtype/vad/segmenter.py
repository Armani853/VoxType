"""Bounded streaming speech segmentation with padding and overlap."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

import numpy as np
import numpy.typing as npt

from voxtype.audio.models import FloatAudio, mono_float32

from .base import VoiceActivityDetector


class SegmentReason(StrEnum):
    SILENCE = "silence"
    PREFERRED_PAUSE = "preferred_pause"
    MAXIMUM_DURATION = "maximum_duration"
    FLUSH = "flush"


@dataclass(frozen=True, slots=True)
class SegmentationSettings:
    frame_ms: int = 32
    speech_threshold: float = 0.45
    minimum_speech_ms: int = 180
    end_silence_ms: int = 650
    pre_roll_ms: int = 400
    post_roll_ms: int = 300
    preferred_segment_seconds: float = 15.0
    maximum_segment_seconds: float = 25.0
    overlap_seconds: float = 0.8
    forced_pause_ms: int = 180

    def __post_init__(self) -> None:
        if not 10 <= self.frame_ms <= 100:
            raise ValueError("frame_ms must be between 10 and 100")
        if not 0.0 < self.speech_threshold < 1.0:
            raise ValueError("speech_threshold must be in (0, 1)")
        if self.pre_roll_ms < 300 or self.post_roll_ms < 250:
            raise ValueError("pre-roll must be >=300 ms and post-roll >=250 ms")
        if self.minimum_speech_ms <= 0 or self.end_silence_ms < self.post_roll_ms:
            raise ValueError("speech/silence durations are inconsistent")
        if not 8.0 <= self.preferred_segment_seconds <= 15.0:
            raise ValueError("preferred segment duration must be between 8 and 15 seconds")
        if self.maximum_segment_seconds < self.preferred_segment_seconds:
            raise ValueError("maximum duration must not be shorter than preferred duration")
        if not 0.8 <= self.overlap_seconds <= 1.5:
            raise ValueError("overlap must be between 0.8 and 1.5 seconds")
        if self.overlap_seconds >= self.preferred_segment_seconds:
            raise ValueError("overlap must be shorter than a segment")

    @classmethod
    def quiet_room(cls) -> SegmentationSettings:
        return cls(speech_threshold=0.40, end_silence_ms=600)

    @classmethod
    def noisy_room(cls) -> SegmentationSettings:
        return cls(
            speech_threshold=0.55, minimum_speech_ms=220, end_silence_ms=750, forced_pause_ms=220
        )


@dataclass(frozen=True, slots=True)
class AudioSegment:
    sequence: int
    samples: FloatAudio = field(repr=False)
    sample_rate: int
    start_sample: int
    end_sample: int
    reason: SegmentReason
    leading_overlap_seconds: float
    speech_seconds: float
    session_id: int | None = None

    def __post_init__(self) -> None:
        audio = mono_float32(self.samples, copy=True)
        audio.setflags(write=False)
        object.__setattr__(self, "samples", audio)
        if self.end_sample < self.start_sample or audio.size != self.end_sample - self.start_sample:
            raise ValueError("segment sample bounds do not match its PCM payload")

    @property
    def forced(self) -> bool:
        return self.reason in (SegmentReason.PREFERRED_PAUSE, SegmentReason.MAXIMUM_DURATION)

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.sample_rate

    @property
    def start_seconds(self) -> float:
        return self.start_sample / self.sample_rate

    @property
    def end_seconds(self) -> float:
        return self.end_sample / self.sample_rate


@dataclass(frozen=True, slots=True)
class _Frame:
    start: int
    samples: FloatAudio = field(repr=False)
    probability: float

    @property
    def end(self) -> int:
        return self.start + int(self.samples.size)


class StreamingSegmenter:
    """Turn an endless PCM stream into bounded ASR work units."""

    def __init__(
        self,
        detector: VoiceActivityDetector,
        *,
        sample_rate: int = 16_000,
        settings: SegmentationSettings | None = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self.detector = detector
        self.session_id: int | None = None
        self.sample_rate = sample_rate
        self.settings = settings or SegmentationSettings()
        self._frame_samples = max(1, round(sample_rate * self.settings.frame_ms / 1_000))
        self._minimum_speech = round(sample_rate * self.settings.minimum_speech_ms / 1_000)
        self._end_silence = round(sample_rate * self.settings.end_silence_ms / 1_000)
        self._pre_roll = round(sample_rate * self.settings.pre_roll_ms / 1_000)
        self._post_roll = round(sample_rate * self.settings.post_roll_ms / 1_000)
        self._forced_pause = round(sample_rate * self.settings.forced_pause_ms / 1_000)
        self._preferred = round(sample_rate * self.settings.preferred_segment_seconds)
        self._maximum = round(sample_rate * self.settings.maximum_segment_seconds)
        self._overlap = round(sample_rate * self.settings.overlap_seconds)
        self._pending: FloatAudio = np.empty(0, dtype=np.float32)
        self._pending_start = 0
        self._stream_end = 0
        self.speech_samples = 0
        self._history: deque[_Frame] = deque()
        self._active: deque[_Frame] = deque()
        self._active_start: int | None = None
        self._speech_run_start: int | None = None
        self._speech_run_samples = 0
        self._silence_samples = 0
        self._last_speech_end = 0
        self._pause_recorded = False
        self._pause_candidates: list[int] = []
        self._leading_overlap = 0
        self._new_content_boundary: int | None = None
        self._segment_sequence = 0

    @property
    def active(self) -> bool:
        return self._active_start is not None

    @property
    def buffered_seconds(self) -> float:
        if self._active_start is None:
            frames = sum(frame.samples.size for frame in self._history) + self._pending.size
        else:
            frames = self._active[-1].end - self._active_start if self._active else 0
        return float(frames / self.sample_rate)

    def process(self, samples: npt.ArrayLike) -> tuple[AudioSegment, ...]:
        audio = mono_float32(samples)
        if audio.size == 0:
            return ()
        if self._pending.size:
            self._pending = cast(FloatAudio, np.concatenate((self._pending, audio)))
        else:
            self._pending = audio.copy()
            self._pending_start = self._stream_end
        self._stream_end += int(audio.size)
        emitted: list[AudioSegment] = []
        while self._pending.size >= self._frame_samples:
            frame_audio = self._pending[: self._frame_samples].copy()
            frame_start = self._pending_start
            self._pending = self._pending[self._frame_samples :].copy()
            self._pending_start += self._frame_samples
            emitted.extend(self._process_frame(frame_audio, frame_start))
        return tuple(emitted)

    def _process_frame(self, samples: FloatAudio, start: int) -> list[AudioSegment]:
        probability = self.detector.speech_probability(samples, self.sample_rate)
        frame = _Frame(start, samples, probability)
        is_speech = probability >= self.settings.speech_threshold
        if is_speech:
            self.speech_samples += samples.size
        if not self.active:
            self._history.append(frame)
            self._trim_history(frame.end)
            if is_speech:
                if self._speech_run_start is None:
                    self._speech_run_start = frame.start
                    self._speech_run_samples = 0
                self._speech_run_samples += int(samples.size)
                if self._speech_run_samples >= self._minimum_speech:
                    self._activate()
            else:
                self._speech_run_start = None
                self._speech_run_samples = 0
            return []

        self._active.append(frame)
        if is_speech:
            self._last_speech_end = frame.end
            self._silence_samples = 0
            self._pause_recorded = False
        else:
            self._silence_samples += int(samples.size)
            if self._silence_samples >= self._forced_pause and not self._pause_recorded:
                candidate = min(frame.end, self._last_speech_end + self._post_roll)
                self._pause_candidates.append(candidate)
                self._pause_recorded = True

        emitted: list[AudioSegment] = []
        if self._silence_samples >= self._end_silence:
            end = min(frame.end, self._last_speech_end + self._post_roll)
            if self._contains_new_speech(end):
                emitted.append(self._build_segment(end, SegmentReason.SILENCE))
            self._finish_natural(end)
            return emitted

        active_start = self._require_active_start()
        duration = frame.end - active_start
        if duration >= self._preferred and self._silence_samples >= self._forced_pause:
            end = min(frame.end, self._last_speech_end + self._post_roll)
            emitted.append(self._build_segment(end, SegmentReason.PREFERRED_PAUSE))
            self._continue_after_forced(end)
        elif duration >= self._maximum:
            minimum_safe = active_start + max(
                self._overlap + self._minimum_speech, self._preferred // 2
            )
            candidates = [
                value
                for value in self._pause_candidates
                if minimum_safe <= value <= active_start + self._maximum
            ]
            if candidates:
                target = active_start + self._preferred
                end = min(candidates, key=lambda value: abs(value - target))
            else:
                end = active_start + self._maximum
            emitted.append(self._build_segment(end, SegmentReason.MAXIMUM_DURATION))
            self._continue_after_forced(end)
        return emitted

    def _trim_history(self, current_end: int) -> None:
        retain = self._pre_roll + self._minimum_speech + self._frame_samples
        threshold = current_end - retain
        while self._history and self._history[0].end <= threshold:
            self._history.popleft()

    def _activate(self) -> None:
        onset = self._speech_run_start
        if onset is None:
            raise RuntimeError("cannot activate VAD without a speech onset")
        start = max(0, onset - self._pre_roll)
        selected = self._slice_frames(self._history, start, self._history[-1].end)
        self._active = deque(selected)
        self._active_start = selected[0].start
        speech_frames = [
            frame for frame in selected if frame.probability >= self.settings.speech_threshold
        ]
        self._last_speech_end = max(frame.end for frame in speech_frames)
        self._silence_samples = 0
        self._pause_recorded = False
        self._pause_candidates.clear()
        self._leading_overlap = 0
        self._new_content_boundary = self._active_start
        self._history.clear()
        self._speech_run_start = None
        self._speech_run_samples = 0

    def _build_segment(self, end: int, reason: SegmentReason) -> AudioSegment:
        start = self._require_active_start()
        frames = self._slice_frames(self._active, start, end)
        samples = (
            np.concatenate([frame.samples for frame in frames])
            if frames
            else np.empty(0, dtype=np.float32)
        )
        speech_samples = 0
        for frame in frames:
            if frame.probability >= self.settings.speech_threshold:
                speech_samples += frame.samples.size
        segment = AudioSegment(
            sequence=self._segment_sequence,
            samples=samples,
            sample_rate=self.sample_rate,
            start_sample=start,
            end_sample=end,
            reason=reason,
            leading_overlap_seconds=self._leading_overlap / self.sample_rate,
            speech_seconds=speech_samples / self.sample_rate,
            session_id=self.session_id,
        )
        self._segment_sequence += 1
        return segment

    def _continue_after_forced(self, boundary: int) -> None:
        old_start = self._require_active_start()
        new_start = max(old_start, boundary - self._overlap)
        current_end = self._active[-1].end
        self._active = deque(self._slice_frames(self._active, new_start, current_end))
        self._active_start = new_start
        self._leading_overlap = boundary - new_start
        self._new_content_boundary = boundary
        self._pause_candidates = [value for value in self._pause_candidates if value > boundary]
        speech_ends = [
            frame.end
            for frame in self._active
            if frame.probability >= self.settings.speech_threshold
        ]
        self._last_speech_end = max(speech_ends, default=boundary)
        trailing = max(0, current_end - self._last_speech_end)
        self._silence_samples = trailing
        self._pause_recorded = trailing >= self._forced_pause

    def _finish_natural(self, boundary: int) -> None:
        current_end = self._active[-1].end
        leftover = self._slice_frames(self._active, boundary, current_end)
        self._clear_active_state()
        self._history = deque(leftover)
        if leftover:
            self._trim_history(leftover[-1].end)

    def _contains_new_speech(self, end: int) -> bool:
        boundary = self._new_content_boundary
        if boundary is None:
            return False
        return any(
            frame.probability >= self.settings.speech_threshold
            and frame.end > boundary
            and frame.start < end
            for frame in self._active
        )

    @staticmethod
    def _slice_frames(frames: deque[_Frame], start: int, end: int) -> list[_Frame]:
        selected: list[_Frame] = []
        for frame in frames:
            left = max(start, frame.start)
            right = min(end, frame.end)
            if left >= right:
                continue
            offset_left = left - frame.start
            offset_right = right - frame.start
            selected.append(
                _Frame(left, frame.samples[offset_left:offset_right].copy(), frame.probability)
            )
        return selected

    def flush(self) -> tuple[AudioSegment, ...]:
        emitted: list[AudioSegment] = []
        if self._pending.size:
            pending = self._pending
            start = self._pending_start
            self._pending = np.empty(0, dtype=np.float32)
            emitted.extend(self._process_frame(pending, start))
            self._pending_start = self._stream_end
        if self.active:
            end = self._active[-1].end
            if self._contains_new_speech(end):
                emitted.append(self._build_segment(end, SegmentReason.FLUSH))
            self._clear_active_state()
        self._history.clear()
        self._speech_run_start = None
        self._speech_run_samples = 0
        return tuple(emitted)

    def _clear_active_state(self) -> None:
        self._active.clear()
        self._active_start = None
        self._silence_samples = 0
        self._last_speech_end = 0
        self._pause_recorded = False
        self._pause_candidates.clear()
        self._leading_overlap = 0
        self._new_content_boundary = None

    def _require_active_start(self) -> int:
        if self._active_start is None:
            raise RuntimeError("segmenter is not active")
        return self._active_start

    def reset(self, *, preserve_sequence: bool = False) -> None:
        next_sequence = self._segment_sequence
        self._pending = np.empty(0, dtype=np.float32)
        self._pending_start = 0
        self._stream_end = 0
        self.speech_samples = 0
        self._history.clear()
        self._clear_active_state()
        self._speech_run_start = None
        self._speech_run_samples = 0
        self._segment_sequence = next_sequence if preserve_sequence else 0
        self.detector.reset()
