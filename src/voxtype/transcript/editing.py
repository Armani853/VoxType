"""Local preview and undo for explicit text edits."""

from __future__ import annotations

import re
from dataclasses import dataclass


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
