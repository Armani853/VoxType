"""Session-owned formatting for explicit structured dictation commands."""

from __future__ import annotations

from dataclasses import dataclass

STRUCTURED_COMMANDS = {
    "начать список": "start_list",
    "следующий пункт": "next_item",
    "закончить список": "end_list",
    "двоеточие": "colon",
    "открыть кавычки": "open_quote",
    "закрыть кавычки": "close_quote",
    "start list": "start_list",
    "next item": "next_item",
    "end list": "end_list",
    "colon": "colon",
    "open quotes": "open_quote",
    "close quotes": "close_quote",
}


@dataclass(slots=True)
class FormattingState:
    list_mode: bool = False
    list_index: int = 0
    quote_open: bool = False


class StructuredDictationFormatter:
    @staticmethod
    def apply(state: FormattingState, command: str, previous: str, language: str) -> str | None:
        if command == "start_list" and not state.list_mode:
            state.list_mode = True
            state.list_index = 0
            command = "next_item"
        if command == "next_item" and state.list_mode:
            state.list_index += 1
            boundary = "\n" if previous and not previous.endswith("\n") else ""
            return f"{boundary}{state.list_index}. "
        if command == "end_list" and state.list_mode:
            state.list_mode = False
            return "\n" if previous and not previous.endswith("\n") else ""
        if command == "colon":
            return ":"
        if command == "open_quote" and not state.quote_open:
            state.quote_open = True
            return "«" if language == "ru" else "“"
        if command == "close_quote" and state.quote_open:
            state.quote_open = False
            return "»" if language == "ru" else "”"
        return None
