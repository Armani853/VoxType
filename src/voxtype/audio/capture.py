"""Real-time-safe microphone ingress with background DSP and disk spillover."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import sleep, time
from typing import Any

import numpy as np
import numpy.typing as npt

from .devices import _sounddevice
from .models import AudioChunk, AudioQueueStats, FloatAudio
from .preprocessing import AudioPreprocessor, PreprocessorSettings
from .resampler import StreamingResampler
from .spool import (
    AudioQueueClosed,
    AudioQueueLease,
    LosslessAudioQueue,
    SpoolCapacityError,
    SpoolError,
)


@dataclass(frozen=True, slots=True)
class AudioCaptureConfig:
    device: int | str | None = None
    input_sample_rate: int = 48_000
    target_sample_rate: int = 16_000
    block_duration_ms: int = 30
    callback_queue_chunks: int = 128

    def __post_init__(self) -> None:
        if self.input_sample_rate <= 0 or self.target_sample_rate <= 0:
            raise ValueError("sample rates must be positive")
        if not 10 <= self.block_duration_ms <= 200:
            raise ValueError("block_duration_ms must be between 10 and 200")
        if self.callback_queue_chunks < 4:
            raise ValueError("callback_queue_chunks must be at least 4")

    @property
    def block_frames(self) -> int:
        return max(1, round(self.input_sample_rate * self.block_duration_ms / 1_000))


@dataclass(frozen=True, slots=True)
class _CallbackBlock:
    samples: FloatAudio
    captured_at: float
    input_overflow: bool


class AudioCaptureService:
    """Copy in PortAudio's callback and do every other operation off-thread."""

    def __init__(
        self,
        output_queue: LosslessAudioQueue,
        config: AudioCaptureConfig | None = None,
        *,
        preprocessing: PreprocessorSettings | None = None,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.output_queue = output_queue
        self.config = config or AudioCaptureConfig()
        self.preprocessor = AudioPreprocessor(self.config.target_sample_rate, preprocessing)
        self.resampler = StreamingResampler(
            self.config.input_sample_rate, self.config.target_sample_rate
        )
        self._stream_factory = stream_factory
        self._callback_queue: Queue[_CallbackBlock] = Queue(
            maxsize=self.config.callback_queue_chunks
        )
        self._stop_requested = Event()
        self._abort_dispatch = Event()
        self._dispatcher: Thread | None = None
        self._stream: Any = None
        self._lifecycle_lock = RLock()
        self._metrics_lock = RLock()
        self._running = False
        self._accept_callbacks = False
        self._next_sequence = output_queue.next_sequence
        # Callback-owned integer updates stay lock-free; the dispatcher publishes
        # their deltas into the synchronized public metrics object.
        self._captured_frames = 0
        self._dropped_frames = 0
        self._callback_overflows = 0
        self._published_captured = 0
        self._published_dropped = 0
        self._published_overflows = 0
        self._fatal_error: Exception | None = None

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            return self._running

    @property
    def fatal_error(self) -> Exception | None:
        return self._fatal_error

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._running:
                return
            self._stop_requested.clear()
            self._abort_dispatch.clear()
            self._fatal_error = None
            self._accept_callbacks = True
            self._dispatcher = Thread(
                target=self._dispatch_loop, name="voxtype-audio-dispatch", daemon=True
            )
            self._dispatcher.start()
            try:
                factory = self._stream_factory
                if factory is None:
                    factory = _sounddevice().InputStream
                self._stream = factory(
                    device=self.config.device,
                    samplerate=self.config.input_sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=self.config.block_frames,
                    callback=self.audio_callback,
                )
                self._stream.start()
            except Exception:
                self._accept_callbacks = False
                self._stop_requested.set()
                stream = self._stream
                self._stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception as exc:
                        self._fatal_error = exc
                dispatcher = self._dispatcher
                if dispatcher is not None:
                    dispatcher.join(timeout=2.0)
                self._dispatcher = None
                raise
            self._running = True

    def audio_callback(
        self, indata: npt.NDArray[np.float32], frames: int, time_info: Any, status: Any
    ) -> None:
        """PortAudio callback: copy and enqueue only; never perform DSP or I/O."""

        del time_info
        if not self._accept_callbacks:
            return
        overflow = bool(getattr(status, "input_overflow", False))
        self._captured_frames += max(int(frames), 0)
        self._callback_overflows += int(overflow)
        copied = np.array(indata, dtype=np.float32, copy=True, order="C").reshape(-1)
        try:
            self._callback_queue.put_nowait(_CallbackBlock(copied, time(), overflow))
        except Full:
            self._dropped_frames += copied.size

    def submit_test_audio(
        self,
        samples: npt.ArrayLike,
        *,
        captured_at: float | None = None,
        input_overflow: bool = False,
    ) -> bool:
        """Feed the same bounded callback path without requiring a microphone."""

        audio = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
        self._captured_frames += int(audio.size)
        self._callback_overflows += int(input_overflow)
        try:
            self._callback_queue.put_nowait(
                _CallbackBlock(audio, captured_at or time(), input_overflow)
            )
        except Full:
            self._dropped_frames += int(audio.size)
            return False
        return True

    def start_dispatcher_for_tests(self) -> None:
        with self._lifecycle_lock:
            if self._dispatcher is not None and self._dispatcher.is_alive():
                return
            self._stop_requested.clear()
            self._abort_dispatch.clear()
            self._accept_callbacks = True
            self._dispatcher = Thread(
                target=self._dispatch_loop, name="voxtype-audio-test-dispatch", daemon=True
            )
            self._dispatcher.start()
            self._running = True

    def _dispatch_loop(self) -> None:
        try:
            while not self._stop_requested.is_set() or not self._callback_queue.empty():
                try:
                    block = self._callback_queue.get(timeout=0.05)
                except Empty:
                    self._publish_callback_metrics()
                    continue
                try:
                    resampled = self.resampler.process(block.samples)
                    if resampled.size:
                        processed = self.preprocessor.process(resampled)
                        self.output_queue.update_levels(
                            processed.input_levels.rms,
                            processed.input_levels.clipping_percentage,
                        )
                        chunk = AudioChunk(
                            self._next_sequence,
                            processed.samples,
                            self.config.target_sample_rate,
                            block.captured_at,
                            block.input_overflow,
                        )
                        if self._put_with_backpressure(chunk):
                            self._next_sequence += 1
                    self._publish_callback_metrics()
                except Exception:
                    self._dropped_frames += int(block.samples.size)
                    raise
                finally:
                    self._callback_queue.task_done()
            tail = self.resampler.flush()
            if tail.size:
                processed = self.preprocessor.process(tail)
                chunk = AudioChunk(
                    self._next_sequence, processed.samples, self.config.target_sample_rate, time()
                )
                if self._put_with_backpressure(chunk):
                    self._next_sequence += 1
        except Exception as exc:
            self._fatal_error = exc
            self._accept_callbacks = False
            self._stop_requested.set()
            self._discard_callback_backlog()
        finally:
            self._publish_callback_metrics()
            self._running = False

    def _put_with_backpressure(self, chunk: AudioChunk) -> bool:
        while True:
            try:
                self.output_queue.put(chunk)
                return True
            except SpoolCapacityError:
                # A consumer may free space.  Retain this block in the dispatcher
                # rather than discarding it while capture is still active.
                if self._abort_dispatch.is_set():
                    self._dropped_frames += self._input_equivalent_frames(chunk.frames)
                    return False
                sleep(0.025)
            except AudioQueueClosed:
                self._dropped_frames += self._input_equivalent_frames(chunk.frames)
                return False
            except SpoolError as exc:
                self._fatal_error = exc
                raise

    def _input_equivalent_frames(self, output_frames: int) -> int:
        return round(output_frames * self.config.input_sample_rate / self.config.target_sample_rate)

    def _discard_callback_backlog(self) -> None:
        while True:
            try:
                block = self._callback_queue.get_nowait()
            except Empty:
                return
            self._dropped_frames += int(block.samples.size)
            self._callback_queue.task_done()

    def _publish_callback_metrics(self) -> None:
        with self._metrics_lock:
            captured = self._captured_frames
            dropped = self._dropped_frames
            overflows = self._callback_overflows
            captured_delta = captured - self._published_captured
            dropped_delta = dropped - self._published_dropped
            overflow_delta = overflows - self._published_overflows
            if captured_delta:
                self.output_queue.record_capture(captured_delta)
            if dropped_delta:
                self.output_queue.record_drop(dropped_delta)
            for _ in range(max(overflow_delta, 0)):
                self.output_queue.record_capture(0, overflow=True)
            self._published_captured = captured
            self._published_dropped = dropped
            self._published_overflows = overflows

    def stop(self, *, drain_timeout: float = 5.0) -> bool:
        with self._lifecycle_lock:
            self._accept_callbacks = False
            stream = self._stream
            self._stream = None
            stream_error: Exception | None = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception as exc:
                    stream_error = exc
                try:
                    stream.close()
                except Exception as exc:
                    stream_error = stream_error or exc
            self._stop_requested.set()
            dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.join(timeout=max(drain_timeout, 0.0))
            if dispatcher.is_alive():
                self._abort_dispatch.set()
                dispatcher.join(timeout=1.0)
        stopped = dispatcher is None or not dispatcher.is_alive()
        with self._lifecycle_lock:
            if stream_error is not None and self._fatal_error is None:
                self._fatal_error = stream_error
            if stopped:
                self._dispatcher = None
            self._running = not stopped
        return stopped

    def stats(self) -> AudioQueueStats:
        self._publish_callback_metrics()
        return self.output_queue.snapshot()

    def acquire(self, timeout: float | None = None) -> AudioQueueLease | None:
        return self.output_queue.acquire(timeout)

    def ack(self, lease: AudioQueueLease) -> None:
        self.output_queue.ack(lease)

    def nack(self, lease: AudioQueueLease) -> None:
        self.output_queue.nack(lease)

    def __enter__(self) -> AudioCaptureService:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()
