from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.levels import InputLevelStatus, LevelAnalyzer
from voxtype.audio.models import AudioChunk, mono_float32
from voxtype.audio.preprocessing import (
    AudioPreprocessor,
    PreprocessingProfile,
    PreprocessorSettings,
)
from voxtype.audio.resampler import StreamingResampler, resample_audio
from voxtype.audio.ring_buffer import AudioRingBuffer
from voxtype.audio.spool import (
    DiskSpool,
    LosslessAudioQueue,
    SpoolCorruptionError,
)


def test_mono_float32_scales_integer_pcm_and_mixes_stereo() -> None:
    stereo = np.array([[32767, 32767], [-32768, -32768], [0, 0]], dtype=np.int16)
    audio = mono_float32(stereo)
    assert audio.dtype == np.float32
    assert audio.shape == (3,)
    assert audio[0] == pytest.approx(32767 / 32768)
    assert audio[1] == -1.0


def test_ring_buffer_retains_exact_newest_capacity() -> None:
    ring = AudioRingBuffer(5)
    ring.append(np.array([1, 2, 3], dtype=np.float32) / 10)
    ring.append(np.array([4, 5, 6, 7], dtype=np.float32) / 10)
    np.testing.assert_allclose(ring.tail(), np.array([3, 4, 5, 6, 7], dtype=np.float32) / 10)
    np.testing.assert_allclose(ring.tail(2), np.array([0.6, 0.7], dtype=np.float32))
    ring.clear()
    assert len(ring) == 0


def test_disk_spool_is_fifo_recoverable_and_acknowledged_only_explicitly(tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    spool = DiskSpool(directory, max_bytes=1_000_000)
    for sequence in (7, 8, 9):
        spool.append(AudioChunk(sequence, np.full(160, sequence / 10, dtype=np.float32)))
    first = spool.acquire()
    assert first is not None and first.sequence == 7
    spool.release(first.sequence)
    spool.close()

    recovered = DiskSpool(directory, max_bytes=1_000_000)
    assert recovered.verify() == 3
    sequences: list[int] = []
    while (chunk := recovered.acquire()) is not None:
        sequences.append(chunk.sequence)
        recovered.ack(chunk.sequence)
    assert sequences == [7, 8, 9]
    assert not tuple(directory.glob("*.vtxpcm"))


def test_disk_spool_detects_payload_corruption(tmp_path: Path) -> None:
    spool = DiskSpool(tmp_path)
    spool.append(AudioChunk(0, np.linspace(-0.5, 0.5, 100, dtype=np.float32)))
    path = next(tmp_path.glob("*.vtxpcm"))
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(data)
    with pytest.raises(SpoolCorruptionError, match="checksum"):
        DiskSpool(tmp_path)


def test_lossless_queue_spills_before_ram_limit_and_preserves_global_fifo(tmp_path: Path) -> None:
    queue = LosslessAudioQueue(
        max_ram_chunks=4,
        spill_threshold=0.5,
        spool=DiskSpool(tmp_path, max_bytes=2_000_000),
    )
    destinations = [
        queue.put(AudioChunk(index, np.full(160, index / 20, dtype=np.float32)))
        for index in range(8)
    ]
    assert destinations[:2] == ["memory", "memory"]
    assert destinations[2:] == ["disk"] * 6
    assert queue.snapshot().ram_chunks <= 4
    assert queue.snapshot().spilling_to_disk

    observed: list[int] = []
    first = queue.acquire(0.1)
    assert first is not None
    queue.nack(first)
    while (lease := queue.acquire(0.01)) is not None:
        observed.append(lease.chunk.sequence)
        queue.ack(lease)
        if len(observed) == 8:
            break
    assert observed == list(range(8))
    stats = queue.snapshot()
    assert stats.queued_audio_seconds == pytest.approx(0.0)
    assert stats.processed_audio_seconds == pytest.approx(0.08)
    assert stats.queue_high_watermark == 8
    assert stats.spooled_chunks == 0


def test_spill_migrates_ram_prefix_so_crash_recovery_has_no_fifo_gap(tmp_path: Path) -> None:
    directory = tmp_path / "recoverable"
    spool = DiskSpool(directory, max_bytes=2_000_000)
    queue = LosslessAudioQueue(max_ram_chunks=4, spill_threshold=0.5, spool=spool)
    for sequence in range(6):
        queue.put(AudioChunk(sequence, np.full(160, 0.1, dtype=np.float32)))
    stats = queue.snapshot()
    assert stats.ram_chunks == 0
    assert stats.spooled_chunks == 6
    spool.close()

    recovered = DiskSpool(directory, max_bytes=2_000_000)
    sequences: list[int] = []
    while (chunk := recovered.acquire()) is not None:
        sequences.append(chunk.sequence)
        recovered.ack(chunk.sequence)
    assert sequences == list(range(6))


def test_callback_ingress_is_bounded_and_turns_exhaustion_into_fatal_error(tmp_path: Path) -> None:
    output = LosslessAudioQueue(max_ram_chunks=2, spool=DiskSpool(tmp_path / "spool"))
    service = AudioCaptureService(
        output,
        AudioCaptureConfig(
            input_sample_rate=16_000,
            callback_queue_chunks=4,
            emergency_queue_chunks=4,
        ),
    )
    block = np.zeros(480, dtype=np.float32)
    assert [service.submit_test_audio(block) for _ in range(9)] == [
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
    ]
    stats = service.stats()
    assert stats.captured_frames == 4_320
    assert stats.dropped_frames == 480
    assert service.fatal_error is not None


def test_portaudio_overflow_can_never_report_zero_dropped_frames(tmp_path: Path) -> None:
    output = LosslessAudioQueue(max_ram_chunks=2, spool=DiskSpool(tmp_path / "spool"))
    service = AudioCaptureService(
        output,
        AudioCaptureConfig(input_sample_rate=16_000, callback_queue_chunks=4),
    )
    assert service.submit_test_audio(np.zeros(480, dtype=np.float32), input_overflow=True)
    stats = service.stats()
    assert stats.callback_overflows == 1
    assert stats.dropped_frames >= 480
    assert service.fatal_error is not None


def test_streaming_resampler_keeps_duration_and_boundary_continuity() -> None:
    source_rate = 48_000
    target_rate = 16_000
    time_axis = np.arange(source_rate, dtype=np.float64) / source_rate
    tone = (0.4 * np.sin(2 * np.pi * 440 * time_axis)).astype(np.float32)
    streaming = StreamingResampler(source_rate, target_rate)
    blocks = [streaming.process(tone[start : start + 1379]) for start in range(0, tone.size, 1379)]
    result = np.concatenate((*blocks, streaming.flush()))
    assert result.size == target_rate
    assert np.sqrt(np.mean(result**2)) > 0.2
    reference = resample_audio(tone, source_rate, target_rate)
    assert reference.size == target_rate


def test_preprocessor_reports_clipping_removes_dc_and_does_not_raise_silence() -> None:
    analyzer = LevelAnalyzer()
    clipped = np.r_[np.ones(200), np.zeros(800)].astype(np.float32)
    levels = analyzer.analyze(clipped)
    assert levels.status is InputLevelStatus.CLIPPING
    assert levels.clipping_percentage == pytest.approx(20.0)

    processor = AudioPreprocessor(
        16_000, PreprocessorSettings.for_profile(PreprocessingProfile.NORMAL)
    )
    silence = processor.process(np.zeros(1600, dtype=np.float32))
    assert np.max(np.abs(silence.samples)) == 0.0
    biased_tone = (0.2 + 0.01 * np.sin(2 * np.pi * 200 * np.arange(3200) / 16_000)).astype(
        np.float32
    )
    processed = processor.process(biased_tone)
    assert abs(float(np.mean(processed.samples[-1600:]))) < 0.01
    assert np.max(np.abs(processed.samples)) <= 1.0


def test_capture_dispatcher_resamples_and_spools_without_callback_dsp(tmp_path: Path) -> None:
    output = LosslessAudioQueue(
        max_ram_chunks=2,
        spill_threshold=0.5,
        spool=DiskSpool(tmp_path, max_bytes=4_000_000),
    )
    service = AudioCaptureService(
        output,
        AudioCaptureConfig(
            input_sample_rate=48_000,
            target_sample_rate=16_000,
            callback_queue_chunks=32,
        ),
    )
    service.start_dispatcher_for_tests()
    block = (0.05 * np.sin(2 * np.pi * 300 * np.arange(1440) / 48_000)).astype(np.float32)
    assert all(service.submit_test_audio(block) for _ in range(10))
    assert service.stop(drain_timeout=5.0)
    assert service.fatal_error is None
    stats = service.stats()
    assert stats.captured_frames == 14_400
    assert stats.dropped_frames == 0
    assert stats.spooled_chunks >= 1

    chunks: list[AudioChunk] = []
    while (lease := output.acquire(0.01)) is not None:
        chunks.append(lease.chunk)
        output.ack(lease)
        if len(chunks) == 10:
            break
    assert [chunk.sequence for chunk in chunks] == list(range(10))
    assert sum(chunk.frames for chunk in chunks) == 4_800
    assert all(chunk.sample_rate == 16_000 for chunk in chunks)


def test_stereo_callback_downmixes_off_thread_and_preserves_fifo(tmp_path: Path) -> None:
    output = LosslessAudioQueue(max_ram_chunks=4, spool=DiskSpool(tmp_path / "stereo"))
    service = AudioCaptureService(
        output,
        AudioCaptureConfig(input_sample_rate=16_000, input_channels=2),
        preprocessing=PreprocessorSettings.for_profile(PreprocessingProfile.RAW),
    )
    service.start_dispatcher_for_tests()
    status = type("Status", (), {"input_overflow": False})()
    for sequence in range(3):
        samples = np.full((480, 2), (sequence + 1) * 0.1, dtype=np.float32)
        service.audio_callback(samples, 480, None, status)
    assert service.stop(drain_timeout=5.0)
    assert service.fatal_error is None
    assert service.stats().captured_frames == 1440
    chunks = []
    while (lease := output.acquire(0.01)) is not None:
        chunks.append(lease.chunk)
        output.ack(lease)
    assert [chunk.sequence for chunk in chunks] == [0, 1, 2]
    assert [chunk.frames for chunk in chunks] == [480, 480, 480]
    assert all(chunk.samples.ndim == 1 for chunk in chunks)
    assert service.stats().dropped_frames == 0


def test_spool_binary_header_is_fixed_width_and_not_pickle(tmp_path: Path) -> None:
    spool = DiskSpool(tmp_path)
    spool.append(AudioChunk(42, np.zeros(10, dtype=np.float32)))
    payload = next(tmp_path.glob("*.vtxpcm")).read_bytes()
    assert payload.startswith(b"VTXPCM1\0")
    assert len(payload) == struct.calcsize("<8sQIdIII") + 10 * 4
