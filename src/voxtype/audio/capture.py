"""Real-time-safe microphone ingress with background DSP and disk spillover."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import sleep, time
from typing import Any

import numpy as np
import numpy.typing as npt

from .devices import _sounddevice
from .models import AudioChunk, AudioFormat, AudioQueueStats, CaptureDownmixer, FloatAudio
from .preprocessing import AudioPreprocessor, NoiseSuppressionBackend, PreprocessorSettings
from .quality import AudioQualityMonitor
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
    input_channels: int = 1
    target_sample_rate: int = 16_000
    block_duration_ms: int = 30
    callback_queue_chunks: int = 128
    # A second bounded RAM hand-off absorbs a temporary dispatcher stall.  Once
    # ingress switches to it, all later callback blocks use the same queue for
    # the remainder of the session, which preserves FIFO order without doing
    # disk I/O or waiting in PortAudio's callback.
    emergency_queue_chunks: int = 8_192

    def __post_init__(self) -> None:
        if self.input_sample_rate <= 0 or self.target_sample_rate <= 0:
            raise ValueError("sample rates must be positive")
        if self.input_channels not in (1, 2):
            raise ValueError("input_channels must be one or two")
        if not 10 <= self.block_duration_ms <= 200:
            raise ValueError("block_duration_ms must be between 10 and 200")
        if self.callback_queue_chunks < 4:
            raise ValueError("callback_queue_chunks must be at least 4")
        if self.emergency_queue_chunks < 4:
            raise ValueError("emergency_queue_chunks must be at least 4")

    @property
    def block_frames(self) -> int:
        return max(1, round(self.input_sample_rate * self.block_duration_ms / 1_000))


@dataclass(frozen=True, slots=True)
class _CallbackBlock:
    samples: FloatAudio
    captured_at: float
    input_overflow: bool
    pre_roll: bool = False


class AudioPreRollBuffer:
    """Dispatcher-owned window bound to one capture format and lifetime."""

    def __init__(self, sample_rate: int) -> None:
        self._rate = sample_rate
        self._limit = max(1, round(sample_rate * 0.350))
        self._frames = 0
        self._blocks: deque[_CallbackBlock] = deque()

    def append(self, block: _CallbackBlock) -> None:
        self._blocks.append(block)
        self._frames += len(block.samples)
        while self._frames > self._limit:
            oldest = self._blocks.popleft()
            trim = min(len(oldest.samples), self._frames - self._limit)
            self._frames -= trim
            if trim < len(oldest.samples):
                self._blocks.appendleft(
                    _CallbackBlock(
                        oldest.samples[trim:],
                        oldest.captured_at + trim / self._rate,
                        oldest.input_overflow,
                        pre_roll=True,
                    )
                )

    def take(self) -> tuple[_CallbackBlock, ...]:
        blocks = tuple(self._blocks)
        self._blocks.clear()
        self._frames = 0
        return blocks


@dataclass(frozen=True, slots=True)
class CaptureFinalization:
    last_accepted_sample: int
    last_consumed_sample: int
    last_published_sample: int
    dropped_samples: int
    device_error: str | None
    complete: bool


class AudioIngressOverflowError(RuntimeError):
    """PortAudio or the bounded callback hand-off could not retain all PCM."""


class AudioCaptureService:
    """Copy in PortAudio's callback and do every other operation off-thread."""

    def __init__(
        self,
        output_queue: LosslessAudioQueue,
        config: AudioCaptureConfig | None = None,
        *,
        preprocessing: PreprocessorSettings | None = None,
        denoiser: NoiseSuppressionBackend | None = None,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.output_queue = output_queue
        self.config = config or AudioCaptureConfig()
        self.preprocessor = AudioPreprocessor(
            self.config.target_sample_rate,
            preprocessing,
            denoiser=denoiser,
        )
        self.input_format = AudioFormat(
            self.config.input_sample_rate,
            self.config.input_channels,
            "float32",
            "default" if self.config.device is None else str(self.config.device),
        )
        self._downmixer = CaptureDownmixer()
        self.resampler = StreamingResampler(
            self.input_format.sample_rate_hz, self.config.target_sample_rate
        )
        self._stream_factory = stream_factory
        self._pre_roll: AudioPreRollBuffer | None = None
        self._session_active = Event()
        self._session_active.set()
        self._callback_queue: Queue[_CallbackBlock] = Queue(
            maxsize=self.config.callback_queue_chunks
        )
        self._emergency_queue: Queue[_CallbackBlock] = Queue(
            maxsize=self.config.emergency_queue_chunks
        )
        self._emergency_mode = False
        self._stop_requested = Event()
        self._abort_dispatch = Event()
        self._dispatcher: Thread | None = None
        self._stream: Any = None
        self._lifecycle_lock = RLock()
        self._metrics_lock = RLock()
        self._running = False
        self._accept_callbacks = False
        self._next_sequence = output_queue.next_sequence
        self._sample_offset = output_queue.next_sample_offset
        # Callback-owned integer updates stay lock-free; the dispatcher publishes
        # their deltas into the synchronized public metrics object.
        self._captured_frames = 0
        self._dropped_frames = 0
        self._callback_overflows = 0
        self._published_captured = 0
        self._published_dropped = 0
        self._published_overflows = 0
        self._fatal_error: Exception | None = None
        self._accepted_frames = 0
        self._consumed_frames = 0
        self.finalization: CaptureFinalization | None = None

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            return self._running

    @property
    def fatal_error(self) -> Exception | None:
        return self._fatal_error

    def start(self, *, cancel: Event | None = None) -> None:
        with self._lifecycle_lock:
            if self._running or (cancel is not None and cancel.is_set()):
                return
            self._stop_requested.clear()
            self._abort_dispatch.clear()
            self._fatal_error = None
            self._emergency_mode = False
            self.finalization = None
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
                    samplerate=self.input_format.sample_rate_hz,
                    channels=self.input_format.channels,
                    dtype=self.input_format.sample_format,
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

    def arm_pre_roll(self) -> None:
        if self.running or self.output_queue.spool is not None:
            raise RuntimeError("Pre-roll requires a fresh capture and an in-memory queue")
        self._pre_roll = AudioPreRollBuffer(self.config.input_sample_rate)
        self._session_active.clear()

    @property
    def pre_roll_armed(self) -> bool:
        return not self._session_active.is_set()

    def activate_session(self) -> None:
        self._session_active.set()

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
        if overflow:
            # PortAudio cannot report the exact number already lost.  Count one
            # callback block conservatively so a session can never claim zero
            # dropped frames after an input overflow.
            self._dropped_frames += max(int(frames), 1)
        copied = np.array(indata, dtype=np.float32, copy=True, order="C")
        if self.config.input_channels == 1:
            copied = copied.reshape(-1)
        block = _CallbackBlock(copied, time(), overflow, not self._session_active.is_set())
        if self._enqueue_callback_block(block):
            self._accepted_frames += len(copied)
        else:
            self._dropped_frames += max(int(frames), 0)
            if self._fatal_error is None:
                self._fatal_error = AudioIngressOverflowError(
                    "The bounded callback hand-off queue was exhausted"
                )
            # Continuing would silently discard every following callback.  The
            # controller observes fatal_error and ends the session recoverably.
            self._accept_callbacks = False

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
        if input_overflow:
            self._dropped_frames += max(int(audio.size), 1)
        if not self._enqueue_callback_block(
            _CallbackBlock(audio, captured_at or time(), input_overflow, self.pre_roll_armed)
        ):
            self._dropped_frames += int(audio.size)
            if self._fatal_error is None:
                self._fatal_error = AudioIngressOverflowError(
                    "The bounded callback hand-off queue was exhausted"
                )
            self._accept_callbacks = False
            return False
        self._accepted_frames += len(audio)
        return True

    def _enqueue_callback_block(self, block: _CallbackBlock) -> bool:
        """Route a callback block without waiting and without reordering PCM."""

        if self._emergency_mode:
            try:
                self._emergency_queue.put_nowait(block)
                return True
            except Full:
                return False
        try:
            self._callback_queue.put_nowait(block)
            return True
        except Full:
            # This is a one-way switch for the session.  Keeping it one-way
            # prevents a newer primary-queue block from overtaking an older
            # emergency-queue block during a producer/consumer race.
            self._emergency_mode = True
            try:
                self._emergency_queue.put_nowait(block)
                return True
            except Full:
                return False

    def start_dispatcher_for_tests(self) -> None:
        with self._lifecycle_lock:
            if self._dispatcher is not None and self._dispatcher.is_alive():
                return
            self._stop_requested.clear()
            self._abort_dispatch.clear()
            self._accept_callbacks = True
            self._emergency_mode = False
            self._dispatcher = Thread(
                target=self._dispatch_loop, name="voxtype-audio-test-dispatch", daemon=True
            )
            self._dispatcher.start()
            self._running = True

    def _dispatch_loop(self) -> None:
        try:
            while not self._stop_requested.is_set() or self._has_callback_backlog():
                queued = self._get_callback_block(timeout=0.05)
                if queued is None:
                    self._publish_callback_metrics()
                    continue
                block, source_queue = queued
                try:
                    pre_roll = self._pre_roll
                    if block.pre_roll and pre_roll is not None:
                        pre_roll.append(block)
                        self._consumed_frames += len(block.samples)
                        continue
                    if pre_roll is not None:
                        for earlier in pre_roll.take():
                            self._process_callback_block(earlier)
                        self._pre_roll = None
                    self._process_callback_block(block)
                    self._consumed_frames += len(block.samples)
                    self._publish_callback_metrics()
                except Exception:
                    self._dropped_frames += int(block.samples.shape[0])
                    raise
                finally:
                    source_queue.task_done()
            if self._pre_roll is not None:
                if self._session_active.is_set():
                    for earlier in self._pre_roll.take():
                        self._process_callback_block(earlier)
                self._pre_roll = None
            self._flush_capture_tail()
        except Exception as exc:
            self._fatal_error = exc
            self._accept_callbacks = False
            self._stop_requested.set()
            self._discard_callback_backlog()
        finally:
            self._pre_roll = None
            self._publish_callback_metrics()
            self._running = False

    def _process_callback_block(self, block: _CallbackBlock) -> None:
        mono = self._downmixer.process(block.samples)
        resampled = self.resampler.process(mono)
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
                self.output_queue.session_id,
                self._sample_offset,
                AudioQualityMonitor.measure(block.samples, self.config.input_sample_rate),
            )
            self._sample_offset += chunk.frames
            if self._put_with_backpressure(chunk):
                self._next_sequence += 1

    def _flush_capture_tail(self) -> None:
        tail = self.resampler.flush()
        if tail.size:
            processed = self.preprocessor.process(tail)
            chunk = AudioChunk(
                self._next_sequence,
                processed.samples,
                self.config.target_sample_rate,
                time(),
                session_id=self.output_queue.session_id,
                sample_offset=self._sample_offset,
            )
            self._sample_offset += chunk.frames
            if self._put_with_backpressure(chunk):
                self._next_sequence += 1

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

    def _has_callback_backlog(self) -> bool:
        return not self._callback_queue.empty() or not self._emergency_queue.empty()

    def _get_callback_block(
        self, timeout: float
    ) -> tuple[_CallbackBlock, Queue[_CallbackBlock]] | None:
        # Primary blocks are always older: emergency mode is one-way.
        try:
            if not self._emergency_mode:
                return self._callback_queue.get(timeout=timeout), self._callback_queue
            return self._callback_queue.get_nowait(), self._callback_queue
        except Empty:
            if not self._emergency_mode:
                return None
        # A switch may have occurred just after the timed primary read.
        # Once switched, no newer block can be written to the primary queue.
        try:
            return self._callback_queue.get_nowait(), self._callback_queue
        except Empty:
            try:
                return self._emergency_queue.get(timeout=timeout), self._emergency_queue
            except Empty:
                return None

    def _discard_callback_backlog(self) -> None:
        for callback_queue in (self._callback_queue, self._emergency_queue):
            while True:
                try:
                    block = callback_queue.get_nowait()
                except Empty:
                    break
                self._dropped_frames += int(block.samples.shape[0])
                callback_queue.task_done()

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
            error = self._fatal_error
            self.finalization = CaptureFinalization(
                self._accepted_frames,
                self._consumed_frames,
                self.output_queue.next_sample_offset,
                self._dropped_frames,
                type(error).__name__ if error is not None else None,
                stopped
                and error is None
                and not self._dropped_frames
                and self._accepted_frames == self._consumed_frames,
            )
        return stopped

    def try_stats(self) -> AudioQueueStats:
        return self.output_queue.try_snapshot()

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
