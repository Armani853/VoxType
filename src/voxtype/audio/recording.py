"""Bounded callback capture for short microphone setup and benchmark recordings."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event
from typing import Any

import numpy as np

from voxtype.audio.devices import InputDevice, _sounddevice, choose_capture_channels
from voxtype.audio.models import AudioInputObservation, CaptureDownmixer
from voxtype.audio.quality import AudioQualityMonitor


def record_input(
    device: InputDevice,
    seconds: float,
    sample_rate: int,
    *,
    progress: Callable[[float], None] | None = None,
    cancel: Event | None = None,
    observe_levels: Callable[[AudioInputObservation], None] | None = None,
) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Record mono PCM; the PortAudio callback only copies into a fixed buffer.

    WDM-KS does not support PortAudio's blocking read API on this Windows host.
    Its two-channel pin is mixed down after capture, outside the callback.
    """

    if seconds <= 0 or sample_rate <= 0:
        raise ValueError("recording duration and rate must be positive")
    frames_required = round(seconds * sample_rate)
    channels = choose_capture_channels(device)
    samples = np.empty((frames_required, channels), dtype=np.float32)
    completed = Event()
    offset = 0
    overflowed = False
    sd = _sounddevice()

    def callback(
        indata: np.ndarray[Any, np.dtype[np.float32]], frames: int, _clock: Any, status: Any
    ) -> None:
        nonlocal offset, overflowed
        overflowed = overflowed or bool(getattr(status, "input_overflow", False))
        count = min(max(frames_required - offset, 0), frames)
        if count:
            samples[offset : offset + count] = indata[:count]
            offset += count
        if offset >= frames_required:
            completed.set()
            raise sd.CallbackStop

    deadline = time.monotonic() + seconds + 2.0
    with sd.InputStream(
        device=device.index,
        samplerate=sample_rate,
        channels=channels,
        dtype="float32",
        blocksize=max(1, round(sample_rate * 0.03)),
        callback=callback,
    ):
        while not completed.is_set():
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Microphone check canceled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("microphone delivered no complete recording")
            completed.wait(min(0.1, remaining))
            if progress is not None:
                progress(min(offset / sample_rate, seconds))
    if overflowed:
        raise RuntimeError("PortAudio reported an input overflow during microphone test")
    if observe_levels is not None:
        observe_levels(AudioQualityMonitor.measure(samples, sample_rate))
    return CaptureDownmixer().process(samples)
