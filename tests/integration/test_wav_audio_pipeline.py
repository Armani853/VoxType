from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.spool import DiskSpool, LosslessAudioQueue
from voxtype.vad.segmenter import StreamingSegmenter


class AmplitudeVAD:
    def speech_probability(self, samples: np.ndarray, sample_rate: int) -> float:
        del sample_rate
        return 0.99 if np.max(np.abs(samples), initial=0.0) >= 0.1 else 0.01

    def reset(self) -> None:
        return None


def test_synthetic_wav_uses_capture_dispatcher_and_vad_pipeline(tmp_path: Path) -> None:
    sample_rate = 16_000
    timeline = np.arange(sample_rate, dtype=np.float32) / sample_rate
    speech = np.sin(2 * np.pi * 220 * timeline).astype(np.float32) * 0.4
    samples = np.concatenate(
        (
            np.zeros(sample_rate // 2, dtype=np.float32),
            speech,
            np.zeros(sample_rate, dtype=np.float32),
        )
    )
    wav_path = tmp_path / "synthetic.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes((samples * 32_767).astype("<i2").tobytes())

    with wave.open(str(wav_path), "rb") as source:
        decoded = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(
            np.float32
        ) / np.float32(32_768)

    spool = DiskSpool(tmp_path / "spool")
    queue = LosslessAudioQueue(max_ram_chunks=4, spool=spool, durable=True)
    capture = AudioCaptureService(
        queue,
        AudioCaptureConfig(
            input_sample_rate=sample_rate,
            target_sample_rate=sample_rate,
            callback_queue_chunks=256,
        ),
    )
    capture.start_dispatcher_for_tests()
    for start in range(0, decoded.size, 480):
        assert capture.submit_test_audio(decoded[start : start + 480])
    assert capture.stop(drain_timeout=5.0)
    queue.close()

    segmenter = StreamingSegmenter(AmplitudeVAD())
    segments = []
    while lease := queue.acquire(timeout=0.1):
        segments.extend(segmenter.process(lease.chunk.samples))
        queue.ack(lease)
    segments.extend(segmenter.flush())
    assert len(segments) == 1
    assert segments[0].speech_seconds >= 0.9
    assert queue.snapshot().dropped_frames == 0
    spool.close()
