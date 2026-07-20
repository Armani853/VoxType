"""Explicit, disableable punctuation-only voice commands."""

from __future__ import annotations

import re

from voxtype.core.models import Language

_RU = {
    "точка с запятой": ";",
    "вопросительный знак": "?",
    "восклицательный знак": "!",
    "новый абзац": "\n\n",
    "новая строка": "\n",
    "открой скобку": "(",
    "закрой скобку": ")",
    "двоеточие": ":",
    "запятая": ",",
    "точка": ".",
    "кавычки": '"',
}
_EN = {
    "exclamation mark": "!",
    "question mark": "?",
    "new paragraph": "\n\n",
    "open parenthesis": "(",
    "close parenthesis": ")",
    "new line": "\n",
    "semicolon": ";",
    "period": ".",
    "comma": ",",
    "colon": ":",
    "quote": '"',
}


class VoiceCommandProcessor:
    """Replace only standalone, whitelisted phrases; never execute user text."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def process(self, text: str, language: Language) -> str:
        if not self.enabled or not text:
            return text
        commands = _RU if language is Language.RU else _EN
        result = text
        for phrase in sorted(commands, key=len, reverse=True):
            result = re.sub(
                rf"(?<![\w-]){re.escape(phrase)}(?![\w-])",
                commands[phrase],
                result,
                flags=re.IGNORECASE,
            )
        result = re.sub(r"[ \t]+([,.;:!?])", r"\1", result)
        result = re.sub(r"([,.;:!?])(?=[^\s\n,.;:!?])", r"\1 ", result)
        result = re.sub(r" *\n *", "\n", result)
        return result.strip()
