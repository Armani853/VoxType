"""Bounded local vocabulary used only as a soft ASR initial prompt."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class Vocabulary:
    def __init__(self, entries: list[str] | None = None, max_entries: int = 200):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: list[str] = []
        for entry in entries or []:
            self.add(entry)

    @property
    def entries(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def add(self, entry: str) -> bool:
        cleaned = " ".join(entry.split()).strip(" ,;\t\r\n")
        if not cleaned or len(cleaned) > 120 or any(ord(char) < 32 for char in cleaned):
            return False
        key = cleaned.casefold()
        if any(existing.casefold() == key for existing in self._entries):
            return False
        if len(self._entries) >= self.max_entries:
            return False
        self._entries.append(cleaned)
        return True

    def remove(self, entry: str) -> bool:
        key = entry.casefold()
        for index, existing in enumerate(self._entries):
            if existing.casefold() == key:
                del self._entries[index]
                return True
        return False

    def initial_prompt(self, max_characters: int = 500) -> str:
        if max_characters < 1:
            return ""
        prompt = "Термины: " + ", ".join(self._entries)
        return prompt[:max_characters].rstrip(" ,") if self._entries else ""

    @classmethod
    def load(cls, path: Path) -> Vocabulary:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
            raise ValueError("Vocabulary must be a JSON string array")
        return cls(list(raw))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self._entries, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
