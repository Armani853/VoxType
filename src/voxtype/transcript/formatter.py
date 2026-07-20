"""Language-neutral spacing and optional sentence capitalization."""

from __future__ import annotations

import re

_NO_SPACE_BEFORE = frozenset(",.!?:;%)]}»…")
_NO_SPACE_AFTER = frozenset("([{«")


def normalize_internal_spacing(text: str) -> str:
    """Normalize horizontal whitespace while retaining requested line breaks."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    normalized = "\n".join(lines)
    if text.startswith(("\n", "\r")) and not normalized.startswith("\n"):
        normalized = "\n" + normalized
    if text.endswith(("\n", "\r")) and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized.strip(" \t\r")


def capitalize_sentence(text: str) -> str:
    characters = list(text)
    should_capitalize = True
    for index, character in enumerate(characters):
        if character.isalpha() and should_capitalize:
            characters[index] = character.upper()
            should_capitalize = False
        elif character in ".!?\n":
            should_capitalize = True
        elif not character.isspace() and character not in "\"'«»()[]{}":
            should_capitalize = False
    return "".join(characters)


def format_fragment_boundary(
    previous: str,
    fragment: str,
    *,
    add_space: bool = True,
    capitalize: bool = True,
) -> str:
    """Return only the insertion text with safe spacing at a committed boundary."""
    current = normalize_internal_spacing(fragment)
    if not current:
        return ""
    if capitalize and (not previous.strip() or previous.rstrip().endswith((".", "!", "?", "\n"))):
        current = capitalize_sentence(current)
    if not previous or not add_space or current.startswith("\n"):
        return current
    left = previous[-1]
    right = current[0]
    if left.isspace() or right.isspace() or right in _NO_SPACE_BEFORE or left in _NO_SPACE_AFTER:
        return current
    return f" {current}"


def join_word_texts(words: list[str] | tuple[str, ...]) -> str:
    """Join ASR word strings whether or not the backend includes leading spaces."""
    output = ""
    for word in words:
        if not word:
            continue
        if (
            not output
            or word[0].isspace()
            or word[0] in _NO_SPACE_BEFORE
            or output[-1] in _NO_SPACE_AFTER
        ):
            output += word
        else:
            output += f" {word}"
    return output.strip()
