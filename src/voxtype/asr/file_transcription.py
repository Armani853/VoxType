"""Local media decoding, timestamped transcription and safe file export."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voxtype.asr.base import ASRBackend, LanguageCode
    from collections.abc import Callable

MEDIA_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}
EXPORT_EXTENSIONS = {".txt", ".json", ".srt", ".vtt", ".docx"}
MAX_MEDIA_BYTES = 2 * 1024**3
MAX_AUDIO_SECONDS = 8 * 60 * 60
SAMPLE_RATE = 16_000
BLOCK_SECONDS = 24


class TranscriptionCanceled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FileSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class FileTranscript:
    language: str
    duration: float
    segments: tuple[FileSegment, ...]

    @property
    def text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)

    def json(self) -> str:
        return json.dumps({"schema_version": 1, **asdict(self), "text": self.text},
                          ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> FileTranscript:
        data = json.loads(text)
        if data["schema_version"] != 1:
            raise ValueError("Неподдерживаемая версия транскрипта")
        return cls(data["language"], data["duration"],
                   tuple(FileSegment(**item) for item in data["segments"]))


def validate_media(source: Path) -> Path:
    source = source.resolve()
    if source.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("Поддерживаются WAV, MP3, M4A и FLAC")
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_MEDIA_BYTES:
        raise ValueError("Файл недоступен, пуст или превышает 2 ГБ")
    return source


class FileTranscriptionService:
    def transcribe(
        self, source: Path, backend: ASRBackend, language: LanguageCode,
        cancel: Event, *, initial_prompt: str = "", hotwords: tuple[str, ...] = (),
        transform: Callable[[str], str] = lambda text: text,
    ) -> FileTranscript:
        import av
        import numpy as np

        from voxtype.asr.base import TranscriptionRequest

        source = validate_media(source)
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        block = np.empty(SAMPLE_RATE * BLOCK_SECONDS, dtype=np.float32)
        fill = offset = 0
        segments: list[FileSegment] = []

        def check_cancel() -> None:
            if cancel.is_set():
                raise TranscriptionCanceled()

        def recognize(samples) -> None:
            nonlocal offset
            check_cancel()
            if offset + samples.size > SAMPLE_RATE * MAX_AUDIO_SECONDS:
                raise ValueError("Длительность аудио превышает 8 часов")
            result = backend.transcribe(TranscriptionRequest(
                samples, language=language, start_offset_seconds=offset / SAMPLE_RATE,
                initial_prompt=initial_prompt, hotwords=hotwords,
            ))
            check_cancel()
            for segment in result.segments:
                caption = transform(segment.text).strip()
                if caption:
                    segments.append(FileSegment(segment.start, segment.end, caption))
            offset += samples.size

        def consume(frames) -> None:
            nonlocal fill
            for frame in frames:
                check_cancel()
                samples = frame.to_ndarray().reshape(-1)
                while samples.size:
                    count = min(samples.size, block.size - fill)
                    block[fill:fill + count] = samples[:count]
                    fill += count
                    samples = samples[count:]
                    if fill == block.size:
                        recognize(block)
                        fill = 0

        with av.open(str(source), mode="r") as media:
            if not media.streams.audio:
                raise ValueError("В файле нет аудиодорожки")
            check_cancel()
            backend.load()
            for frame in media.decode(audio=0):
                check_cancel()
                frame.pts = None
                consume(resampler.resample(frame))
            consume(resampler.resample(None))
        if fill:
            recognize(block[:fill])
        if not offset:
            raise ValueError("Декодер не вернул аудио")
        return FileTranscript(language, offset / SAMPLE_RATE, tuple(segments))


def _subtitle_time(seconds: float, separator: str) -> str:
    hours, rest = divmod(round(seconds * 1000), 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def export_transcript(
    transcript: FileTranscript, destination: Path, *, source: Path | None = None,
    overwrite: bool = False,
) -> None:
    destination = destination.resolve()
    if source is not None and (
        destination == source.resolve()
        or (destination.exists() and source.exists() and destination.samefile(source))
    ):
        raise ValueError("Файл экспорта совпадает с исходным аудио")
    extension = destination.suffix.lower()
    if extension not in EXPORT_EXTENSIONS:
        raise ValueError("Поддерживаются TXT, JSON, SRT, VTT и DOCX")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        if extension == ".docx":
            from docx import Document

            document = Document()
            for segment in transcript.segments:
                document.add_paragraph(segment.text)
            document.save(str(temporary))
        else:
            text = transcript.text
            if extension == ".json":
                text = transcript.json()
            elif extension in {".vtt", ".srt"}:
                vtt = extension == ".vtt"
                separator = "." if vtt else ","
                cues = []
                for index, segment in enumerate(transcript.segments, 1):
                    if not math.isfinite(segment.start) or not math.isfinite(segment.end):
                        raise ValueError("В транскрипте неверные метки времени")
                    start = _subtitle_time(segment.start, separator)
                    end = _subtitle_time(segment.end, separator)
                    prefix = "" if vtt else f"{index}\n"
                    lines = "\n".join(line for line in segment.text.splitlines() if line.strip())
                    if vtt:
                        from html import escape

                        lines = escape(lines, quote=False)
                    cues.append(f"{prefix}{start} --> {end}\n{lines}")
                text = ("WEBVTT\n\n" if vtt else "") + "\n\n".join(cues) + "\n"
            temporary.write_text(text, encoding="utf-8")
        if overwrite:
            os.replace(temporary, destination)
        elif os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
