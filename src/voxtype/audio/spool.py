"""Crash-recoverable FIFO PCM spool and a bounded-memory audio queue."""

from __future__ import annotations

import os
import struct
import uuid
import zlib
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock
from time import monotonic
from typing import Literal

import numpy as np

from .models import AudioChunk, AudioQueueStats


class SpoolError(RuntimeError):
    """Base error for durable audio queue failures."""


class SpoolCapacityError(SpoolError):
    """The configured disk safety limit would be exceeded."""


class SpoolCorruptionError(SpoolError):
    """A persisted PCM record failed structural or checksum validation."""


class AudioQueueClosed(RuntimeError):
    """No more audio may be submitted to this queue."""


_MAGIC = b"VTXPCM1\0"
_HEADER = struct.Struct("<8sQIdIII")
_SUFFIX = ".vtxpcm"


@dataclass(frozen=True, slots=True)
class _SpoolEntry:
    sequence: int
    path: Path
    sample_rate: int
    frames: int
    bytes_on_disk: int

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate


class DiskSpool:
    """One atomically published, checksummed file per PCM chunk.

    A chunk is first written to a uniquely named temporary file, flushed, and
    then renamed.  Startup scans only completed records, so a power loss cannot
    turn a partial write into valid audio. ``acquire``/``ack`` keeps the file on
    disk until downstream processing confirms success.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        max_bytes: int = 4 * 1024**3,
        durable_writes: bool = False,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.durable_writes = durable_writes
        self._entries: OrderedDict[int, _SpoolEntry] = OrderedDict()
        self._leased: set[int] = set()
        self._bytes = 0
        self._closed = False
        self._lock = RLock()
        self._load_existing()

    def _load_existing(self) -> None:
        entries: list[_SpoolEntry] = []
        for path in sorted(self.directory.glob(f"*{_SUFFIX}")):
            entry = self._inspect(path, verify_payload=True)
            entries.append(entry)
        entries.sort(key=lambda item: item.sequence)
        for entry in entries:
            if entry.sequence in self._entries:
                raise SpoolCorruptionError(f"duplicate spool sequence {entry.sequence}")
            self._entries[entry.sequence] = entry
            self._bytes += entry.bytes_on_disk
        if self._bytes > self.max_bytes:
            raise SpoolCapacityError("existing spool exceeds the configured safety limit")

    def _inspect(self, path: Path, *, verify_payload: bool) -> _SpoolEntry:
        try:
            with path.open("rb") as stream:
                header = stream.read(_HEADER.size)
                if len(header) != _HEADER.size:
                    raise SpoolCorruptionError(f"truncated spool header: {path.name}")
                magic, sequence, sample_rate, _captured_at, _flags, frames, checksum = (
                    _HEADER.unpack(header)
                )
                if magic != _MAGIC or sample_rate <= 0:
                    raise SpoolCorruptionError(f"invalid spool header: {path.name}")
                if path.name != f"{sequence:020d}{_SUFFIX}":
                    raise SpoolCorruptionError(f"spool filename/header mismatch: {path.name}")
                expected_size = _HEADER.size + frames * np.dtype("<f4").itemsize
                actual_size = path.stat().st_size
                if expected_size != actual_size:
                    raise SpoolCorruptionError(f"invalid spool length: {path.name}")
                if verify_payload:
                    payload = stream.read()
                    if zlib.crc32(payload) & 0xFFFFFFFF != checksum:
                        raise SpoolCorruptionError(f"PCM checksum mismatch: {path.name}")
        except OSError as exc:
            raise SpoolError(f"cannot inspect spool record {path}") from exc
        return _SpoolEntry(sequence, path, sample_rate, frames, actual_size)

    def append(self, chunk: AudioChunk) -> None:
        payload = chunk.samples.astype("<f4", copy=False).tobytes(order="C")
        checksum = zlib.crc32(payload) & 0xFFFFFFFF
        flags = int(chunk.input_overflow)
        header = _HEADER.pack(
            _MAGIC,
            chunk.sequence,
            chunk.sample_rate,
            chunk.captured_at,
            flags,
            chunk.frames,
            checksum,
        )
        record_size = len(header) + len(payload)
        final_path = self.directory / f"{chunk.sequence:020d}{_SUFFIX}"
        temp_path = self.directory / f".{chunk.sequence:020d}.{uuid.uuid4().hex}.tmp"
        with self._lock:
            if self._closed:
                raise SpoolError("spool is closed")
            if chunk.sequence in self._entries or final_path.exists():
                raise SpoolError(f"duplicate spool sequence {chunk.sequence}")
            if self._bytes + record_size > self.max_bytes:
                raise SpoolCapacityError("audio spool disk limit reached")
            try:
                with temp_path.open("xb") as stream:
                    stream.write(header)
                    stream.write(payload)
                    stream.flush()
                    if self.durable_writes:
                        os.fsync(stream.fileno())
                os.replace(temp_path, final_path)
                if self.durable_writes and os.name != "nt":
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            except OSError as exc:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    # The uniquely named temporary file is ignored during
                    # recovery and can safely remain for diagnostics.
                    _ = temp_path
                raise SpoolError(f"cannot persist PCM chunk {chunk.sequence}") from exc
            entry = _SpoolEntry(
                chunk.sequence, final_path, chunk.sample_rate, chunk.frames, record_size
            )
            self._entries[chunk.sequence] = entry
            self._entries = OrderedDict(sorted(self._entries.items()))
            self._bytes += record_size

    def acquire(self) -> AudioChunk | None:
        with self._lock:
            for sequence, entry in self._entries.items():
                if sequence in self._leased:
                    continue
                chunk = self._read(entry)
                self._leased.add(sequence)
                return chunk
        return None

    def _read(self, entry: _SpoolEntry) -> AudioChunk:
        try:
            with entry.path.open("rb") as stream:
                header = stream.read(_HEADER.size)
                magic, sequence, sample_rate, captured_at, flags, frames, checksum = _HEADER.unpack(
                    header
                )
                payload = stream.read()
        except (OSError, struct.error) as exc:
            raise SpoolCorruptionError(f"cannot read spool record {entry.path.name}") from exc
        if magic != _MAGIC or sequence != entry.sequence or frames != entry.frames:
            raise SpoolCorruptionError(f"spool metadata changed: {entry.path.name}")
        if zlib.crc32(payload) & 0xFFFFFFFF != checksum:
            raise SpoolCorruptionError(f"PCM checksum mismatch: {entry.path.name}")
        samples = np.frombuffer(payload, dtype="<f4").astype(np.float32, copy=True)
        return AudioChunk(sequence, samples, sample_rate, captured_at, bool(flags & 1))

    def ack(self, sequence: int) -> None:
        with self._lock:
            entry = self._entries.get(sequence)
            if entry is None or sequence not in self._leased:
                raise SpoolError(f"spool sequence {sequence} is not leased")
            try:
                entry.path.unlink()
            except OSError as exc:
                raise SpoolError(f"cannot delete acknowledged spool record {sequence}") from exc
            del self._entries[sequence]
            self._leased.remove(sequence)
            self._bytes -= entry.bytes_on_disk

    def release(self, sequence: int) -> None:
        with self._lock:
            if sequence not in self._leased:
                raise SpoolError(f"spool sequence {sequence} is not leased")
            self._leased.remove(sequence)

    def popleft(self) -> AudioChunk | None:
        chunk = self.acquire()
        if chunk is not None:
            self.ack(chunk.sequence)
        return chunk

    def contains(self, sequence: int) -> bool:
        with self._lock:
            return sequence in self._entries

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def bytes_on_disk(self) -> int:
        with self._lock:
            return self._bytes

    @property
    def queued_seconds(self) -> float:
        with self._lock:
            return sum(entry.duration_seconds for entry in self._entries.values())

    @property
    def highest_sequence(self) -> int:
        with self._lock:
            return next(reversed(self._entries), -1)

    @property
    def oldest_available_sequence(self) -> int | None:
        with self._lock:
            return next(
                (sequence for sequence in self._entries if sequence not in self._leased),
                None,
            )

    def verify(self) -> int:
        with self._lock:
            entries = tuple(self._entries.values())
        for entry in entries:
            self._inspect(entry.path, verify_payload=True)
        return len(entries)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def __enter__(self) -> DiskSpool:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class AudioQueueLease:
    """An item remains unprocessed until its token is acknowledged."""

    token: int
    chunk: AudioChunk
    source: Literal["memory", "disk"]


class LosslessAudioQueue:
    """Bound RAM use and switch monotonically to a durable FIFO spool."""

    def __init__(
        self,
        *,
        max_ram_chunks: int,
        spool: DiskSpool | None,
        spill_threshold: float = 0.75,
        max_inflight: int | None = None,
        durable: bool = False,
        session_id: int | None = None,
    ) -> None:
        if max_ram_chunks <= 0:
            raise ValueError("max_ram_chunks must be positive")
        if not 0.0 < spill_threshold <= 1.0:
            raise ValueError("spill_threshold must be in (0, 1]")
        self.max_ram_chunks = max_ram_chunks
        self.session_id = session_id
        self.spool = spool
        self._spill_at = max(1, min(max_ram_chunks, int(max_ram_chunks * spill_threshold + 0.999)))
        self._max_inflight = max_inflight or max_ram_chunks
        self._durable = durable
        self._ram: deque[AudioChunk] = deque()
        self._inflight: dict[int, AudioQueueLease] = {}
        self._next_token = 1
        self._spilling = spool is not None and spool.count > 0
        self._closed = False
        self._condition = Condition(RLock())
        self._captured_frames = 0
        self._dropped_frames = 0
        self._callback_overflows = 0
        self._processed_seconds = 0.0
        self._queued_seconds = spool.queued_seconds if spool else 0.0
        self._high_watermark = spool.count if spool else 0
        self._current_rms = 0.0
        self._clipping = 0.0
        self._last_sequence = spool.highest_sequence if spool else -1

    @property
    def next_sequence(self) -> int:
        with self._condition:
            return self._last_sequence + 1

    def put(self, chunk: AudioChunk) -> Literal["memory", "disk"]:
        with self._condition:
            if self._closed:
                raise AudioQueueClosed("audio queue is closed")
            if chunk.sequence <= self._last_sequence:
                raise ValueError("audio chunk sequences must be strictly increasing")
            spool = self.spool
            if spool is not None and (self._durable or self._spilling or spool.count > 0
                                      or len(self._ram) >= self._spill_at):
                self._spilling = True
                # Once backlog reaches disk, persist the older RAM prefix first.
                # A process crash can then recover a contiguous FIFO, rather than
                # finding later disk chunks after an irrecoverable memory gap.
                while self._ram:
                    spool.append(self._ram[0])
                    self._ram.popleft()
                spool.append(chunk)
                destination: Literal["memory", "disk"] = "disk"
            else:
                if spool is None and len(self._ram) + len(self._inflight) >= self.max_ram_chunks:
                    raise SpoolCapacityError("In-memory audio buffer is full")
                self._ram.append(chunk)
                destination = "memory"
            self._last_sequence = chunk.sequence
            self._queued_seconds += chunk.duration_seconds
            depth = len(self._ram) + (spool.count if spool else 0) + len(self._inflight)
            self._high_watermark = max(self._high_watermark, depth)
            self._condition.notify_all()
            return destination

    def acquire(self, timeout: float | None = None) -> AudioQueueLease | None:
        deadline = None if timeout is None else monotonic() + max(timeout, 0.0)
        with self._condition:
            while True:
                if len(self._inflight) < self._max_inflight:
                    disk_sequence = self.spool.oldest_available_sequence if self.spool else None
                    if self._ram and (
                        disk_sequence is None or self._ram[0].sequence < disk_sequence
                    ):
                        memory_chunk = self._ram.popleft()
                        lease = self._new_lease(memory_chunk, "memory")
                        return lease
                    disk_chunk = self.spool.acquire() if self.spool else None
                    if disk_chunk is not None:
                        lease = self._new_lease(disk_chunk, "disk")
                        return lease
                if self._closed and not self._ram and (self.spool is None or self.spool.count == 0):
                    return None
                if deadline is not None:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(remaining)
                else:
                    self._condition.wait()

    def _new_lease(self, chunk: AudioChunk, source: Literal["memory", "disk"]) -> AudioQueueLease:
        token = self._next_token
        self._next_token += 1
        lease = AudioQueueLease(token, chunk, source)
        self._inflight[token] = lease
        return lease

    def ack(self, lease: AudioQueueLease) -> None:
        with self._condition:
            current = self._inflight.get(lease.token)
            if current != lease:
                raise ValueError("unknown or already completed audio lease")
            if lease.source == "disk" and self.spool is not None:
                self.spool.ack(lease.chunk.sequence)
            del self._inflight[lease.token]
            duration = lease.chunk.duration_seconds
            self._queued_seconds = max(0.0, self._queued_seconds - duration)
            self._processed_seconds += duration
            if self.spool is not None and self.spool.count == 0 and not any(
                item.source == "disk" for item in self._inflight.values()
            ):
                self._spilling = False
            self._condition.notify_all()

    def nack(self, lease: AudioQueueLease) -> None:
        with self._condition:
            current = self._inflight.get(lease.token)
            if current != lease:
                raise ValueError("unknown or already completed audio lease")
            if lease.source == "disk" and self.spool is not None:
                self.spool.release(lease.chunk.sequence)
            else:
                self._ram.appendleft(lease.chunk)
            del self._inflight[lease.token]
            self._condition.notify_all()

    def get(self, timeout: float | None = None) -> AudioChunk | None:
        """Compatibility helper for consumers that accept at-most-once delivery."""

        lease = self.acquire(timeout)
        if lease is None:
            return None
        self.ack(lease)
        return lease.chunk

    def record_capture(self, frames: int, *, overflow: bool = False) -> None:
        with self._condition:
            self._captured_frames += max(frames, 0)
            if overflow:
                self._callback_overflows += 1

    def record_drop(self, frames: int) -> None:
        with self._condition:
            self._dropped_frames += max(frames, 0)

    def update_levels(self, rms: float, clipping_percentage: float) -> None:
        with self._condition:
            self._current_rms = max(rms, 0.0)
            self._clipping = min(100.0, max(clipping_percentage, 0.0))

    def snapshot(self) -> AudioQueueStats:
        with self._condition:
            return AudioQueueStats(
                captured_frames=self._captured_frames,
                dropped_frames=self._dropped_frames,
                queued_audio_seconds=self._queued_seconds,
                processed_audio_seconds=self._processed_seconds,
                queue_high_watermark=self._high_watermark,
                callback_overflows=self._callback_overflows,
                current_input_rms=self._current_rms,
                clipping_percentage=self._clipping,
                ram_chunks=len(self._ram),
                spooled_chunks=self.spool.count if self.spool else 0,
                spooled_bytes=self.spool.bytes_on_disk if self.spool else 0,
                spilling_to_disk=self._spilling,
            )

    def wait_until_empty(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else monotonic() + max(timeout, 0.0)
        with self._condition:
            while self._ram or (self.spool is not None and self.spool.count) or self._inflight:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed
