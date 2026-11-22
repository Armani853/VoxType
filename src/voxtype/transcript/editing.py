"""Local preview and undo for explicit text edits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import uuid4


class RevisionSource(StrEnum):
    RAW_ASR = "raw_asr"
    FORMATTED = "formatted"
    USER_EDIT = "user_edit"
    VOICE_EDIT = "voice_edit"
    RESTORED = "restored"


@dataclass(frozen=True, slots=True)
class TranscriptRevision:
    id: str
    number: int
    created_at_utc: str
    source: RevisionSource
    text: str = field(repr=False)


@dataclass(slots=True)
class TranscriptRevisions:
    id: str = field(default_factory=lambda: uuid4().hex)
    entries: list[TranscriptRevision] = field(default_factory=list)

    @property
    def number(self) -> int:
        return self.entries[-1].number if self.entries else 0

    def append(self, text: str, source: RevisionSource) -> None:
        if self.entries and self.entries[-1].text == text:
            return
        self.entries.append(
            TranscriptRevision(
                uuid4().hex, self.number + 1, datetime.now(UTC).isoformat(), source, text
            )
        )
        if len(self.entries) > 32:
            if self.entries[0].source is RevisionSource.RAW_ASR:
                del self.entries[1]
            else:
                del self.entries[0]


@dataclass(frozen=True, slots=True)
class VoiceEditRequest:
    transcript_id: str
    source_revision: int
    operation: str
    token_count: int
    replacement: str = field(repr=False)


class VoiceEditCommandInterpreter:
    _WORD = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*")
    _COUNT: ClassVar[dict[str, int]] = {
        "слово": 1,
        "три слова": 3,
        "word": 1,
        "three words": 3,
    }
    _COMMANDS = (
        (re.compile(r"удали последн(?:ее|ие) (слово|три слова)", re.I), "delete"),
        (re.compile(r"delete last (word|three words)", re.I), "delete"),
        (
            re.compile(r"замени последн(?:ее|ие) (слово|три слова) на (.+)", re.I),
            "replace",
        ),
        (re.compile(r"replace last (word|three words) with (.+)", re.I), "replace"),
    )

    @classmethod
    def parse(cls, speech: str, transcript_id: str, revision: int) -> VoiceEditRequest:
        content = speech.strip()
        phrase = content.rstrip(".!?").strip()
        if phrase.casefold() in {"отмени последнее исправление", "undo last correction"}:
            return VoiceEditRequest(transcript_id, revision, "undo", 0, "")
        if phrase.casefold() in {"открой редактор", "open editor"}:
            return VoiceEditRequest(transcript_id, revision, "open", 0, "")
        for pattern, operation in cls._COMMANDS:
            match = pattern.fullmatch(phrase)
            if match:
                return VoiceEditRequest(
                    transcript_id,
                    revision,
                    operation,
                    cls._COUNT[match[1].casefold()],
                    match[2] if operation == "replace" else "",
                )
        raise ValueError("Голосовая правка не распознана")

    @classmethod
    def apply(cls, request: VoiceEditRequest, history: TranscriptRevisions, text: str) -> str:
        if history.id != request.transcript_id or history.number != request.source_revision:
            raise ValueError("Текст изменился после запуска голосовой правки")
        if request.operation == "open":
            return text
        if request.operation == "undo":
            if (
                len(history.entries) < 2
                or history.entries[-1].source is not RevisionSource.VOICE_EDIT
            ):
                raise ValueError("Нет последней голосовой правки для отмены")
            return history.entries[-2].text
        words = list(cls._WORD.finditer(text))
        if not 0 < request.token_count <= len(words):
            raise ValueError("В тексте недостаточно слов для этой правки")
        start, end = words[-request.token_count].start(), words[-1].end()
        if request.operation == "replace" and request.replacement:
            return text[:start] + request.replacement + text[end:]
        if request.operation == "delete":
            prefix = text[:start].rstrip()
            tail = text[end:]
            return prefix + tail if prefix or not re.fullmatch(r"[.,!?;:\s]*", tail) else ""
        raise ValueError("Неподдерживаемая голосовая правка")


@dataclass(slots=True)
class TextEditService:
    previous: str | None = None

    @staticmethod
    def preview(text: str, operation: str, source: str = "", replacement: str = "") -> str:
        if operation == "Uppercase":
            return text.upper()
        if operation == "Lowercase":
            return text.lower()
        if operation == "Sentence case":
            return re.sub(
                r"(^|[.!?]\s+|\n\s*)([^\W\d_])",
                lambda m: m[1] + m[2].upper(),
                text,
            )
        if operation == "Remove duplicate spaces":
            return re.sub(r"[^\S\r\n]{2,}", " ", text)
        if operation == "Replace literal phrase":
            if not source:
                raise ValueError("Укажите фразу для замены")
            return text.replace(source, replacement)
        if operation == "Insert bullet list formatting":
            return "\n".join(
                "• " + line.lstrip() if line.strip() else "" for line in text.split("\n")
            )
        if operation == "Normalize line breaks":
            return text.replace("\r\n", "\n").replace("\r", "\n")
        raise ValueError("Неизвестный способ правки")

    def apply(self, previous: str, new_text: str) -> str:
        self.previous = previous
        return new_text

    def undo(self, current: str) -> str:
        if self.previous is None:
            return current
        previous, self.previous = self.previous, None
        return previous
