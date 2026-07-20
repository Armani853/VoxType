"""Inspect VoxType technical logs without printing potentially sensitive lines."""

from __future__ import annotations

import argparse
import glob
import json
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

_TRANSCRIPT_VALUE = re.compile(
    r"(?i)\b(?:transcript|committed_text|recognized_text|insertion_text)\b\s*[:=]\s*"
    r"(?:\"[^\"]+\"|'[^']+'|\S+)"
)
_PCM_VALUE = re.compile(
    r"(?i)\b(?:pcm|samples|audio_bytes|audio_base64|waveform)\b\s*[:=]\s*"
    r"(?:\"[^\"]+\"|'[^']+'|\[[^]]+\]|\S+)"
)
_ERROR_LEVEL = re.compile(r"(?i)\b(?:ERROR|CRITICAL)\b")
_SESSION_SCOPED = re.compile(r"(?i)(?:chunk|inference|rtf|queue|recovery|injection)")
_SESSION_ID = re.compile(r"(?i)[\"']?(?:session_id|correlation_id)[\"']?\s*[:=]\s*[\"']?[\w-]+")


@dataclass(frozen=True, slots=True)
class LogAudit:
    files: int
    lines: int
    error_records: int
    state_transitions: int
    transcript_violations: int
    pcm_violations: int
    session_context_warnings: int
    unreadable_files: int

    @property
    def privacy_safe(self) -> bool:
        return self.transcript_violations == 0 and self.pcm_violations == 0


def _expand_paths(arguments: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for argument in arguments:
        raw = str(argument)
        if any(character in raw for character in "*?["):
            found.update(Path(item).resolve() for item in glob.glob(raw, recursive=True))
        elif argument.is_dir():
            found.update(path.resolve() for path in argument.rglob("*.log*"))
        elif argument.is_file():
            found.add(argument.resolve())
    return sorted(found)


def _nonempty_sensitive_json(value: object, sensitive_keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in sensitive_keys and child not in (None, "", 0):
                return True
            if _nonempty_sensitive_json(child, sensitive_keys):
                return True
    elif isinstance(value, list):
        return any(_nonempty_sensitive_json(item, sensitive_keys) for item in value)
    return False


def audit_logs(paths: list[Path]) -> LogAudit:
    files = _expand_paths(paths)
    line_count = 0
    errors = 0
    transitions = 0
    transcript_violations = 0
    pcm_violations = 0
    session_warnings = 0
    unreadable = 0
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            unreadable += 1
            continue
        for line in lines:
            line_count += 1
            parsed: object | None = None
            with suppress(json.JSONDecodeError):
                parsed = json.loads(line)
            transcript_found = _TRANSCRIPT_VALUE.search(line) is not None
            pcm_found = _PCM_VALUE.search(line) is not None
            if parsed is not None:
                transcript_found = transcript_found or _nonempty_sensitive_json(
                    parsed,
                    {"transcript", "committed_text", "recognized_text", "insertion_text"},
                )
                pcm_found = pcm_found or _nonempty_sensitive_json(
                    parsed,
                    {"pcm", "samples", "audio_bytes", "audio_base64", "waveform"},
                )
            transcript_violations += int(transcript_found)
            pcm_violations += int(pcm_found)
            errors += int(_ERROR_LEVEL.search(line) is not None)
            transitions += int("state_transition" in line.casefold())
            session_warnings += int(
                _SESSION_SCOPED.search(line) is not None and _SESSION_ID.search(line) is None
            )
    return LogAudit(
        files=len(files),
        lines=line_count,
        error_records=errors,
        state_transitions=transitions,
        transcript_violations=transcript_violations,
        pcm_violations=pcm_violations,
        session_context_warnings=session_warnings,
        unreadable_files=unreadable,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--fail-on-errors", action="store_true")
    arguments = parser.parse_args()
    audit = audit_logs(arguments.paths)
    if arguments.as_json:
        print(json.dumps({**asdict(audit), "privacy_safe": audit.privacy_safe}, indent=2))
    else:
        print(
            f"files={audit.files} lines={audit.lines} errors={audit.error_records} "
            f"state_transitions={audit.state_transitions} "
            f"transcript_violations={audit.transcript_violations} "
            f"pcm_violations={audit.pcm_violations} "
            f"session_context_warnings={audit.session_context_warnings}"
        )
    if not audit.privacy_safe:
        return 2
    if audit.unreadable_files or (arguments.fail_on_errors and audit.error_records):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
