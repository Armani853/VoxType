from time import monotonic

import numpy as np

from voxtype.asr.base import TranscriptionResult, TranscriptionSegment, WordTimestamp
from voxtype.asr.live import LiveSpeechSession


def _result(*words: WordTimestamp) -> TranscriptionResult:
    text = " ".join(word.word for word in words)
    return TranscriptionResult(
        text=text,
        language="ru",
        segments=(TranscriptionSegment(text, words[0].start, words[-1].end, words),),
        audio_duration_seconds=1,
        processing_seconds=0.1,
        model_id="test",
        backend_name="test",
    )


def test_rolling_preview_keeps_absolute_offsets_and_discards_retired_revisions() -> None:
    sample_rate = 1000
    live = LiveSpeechSession("session", "ru", "test", sample_rate)
    audio = np.zeros(1000, dtype=np.float32)
    for _ in range(8):
        live.offer(audio)
    now = monotonic()
    first = live.request(8000, now)
    assert first is not None and first.start_offset_seconds == 0
    word = WordTimestamp("комета", 2.2, 2.5)
    live.publish(first, _result(word), 0.1)
    assert live.snapshot is not None
    assert live.snapshot.unstable_text == "комета"

    live.offer(audio)
    second = live.request(9000, now + 1)
    assert second is not None and second.start_offset_seconds == 1
    assert second.audio.size == 8000
    last = WordTimestamp("летит", 8.3, 8.6)
    live.publish(second, _result(word, last), 0.1)
    assert live.snapshot is not None
    assert live.snapshot.stable_segments[0].start_sample == 2200
    assert live.snapshot.unstable_text == "летит"

    live.finish_segment(9000, 8500)
    live.publish(second, _result(word, last), 0.1)
    assert live.snapshot is None
    live.offer(audio)
    third = live.request(10000, now + 2)
    assert third is not None and third.start_offset_seconds == 8.5
    assert third.audio.size == 1500
