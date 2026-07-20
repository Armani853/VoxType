from __future__ import annotations

import json
from pathlib import Path

from tools.inspect_logs import audit_logs


def test_log_audit_counts_errors_transitions_and_session_context(tmp_path: Path) -> None:
    log = tmp_path / "voxtype.log"
    records = [
        {
            "level": "INFO",
            "event": "state_transition",
            "session_id": "fixture-session",
            "state": "LISTENING",
        },
        {
            "level": "ERROR",
            "event": "inference_failed",
            "session_id": "fixture-session",
            "exception_type": "RuntimeError",
        },
        {"level": "INFO", "event": "queue_depth", "queue_size": 2},
    ]
    log.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    audit = audit_logs([tmp_path])

    assert audit.files == 1
    assert audit.lines == 3
    assert audit.error_records == 1
    assert audit.state_transitions == 1
    assert audit.session_context_warnings == 1
    assert audit.privacy_safe


def test_log_audit_flags_sensitive_fields_without_exposing_values(tmp_path: Path) -> None:
    log = tmp_path / "voxtype.log.1"
    log.write_text(
        "\n".join(
            (
                '{"event":"commit","committed_text":"private fixture"}',
                "INFO audio pcm=[0.1,0.2]",
            )
        ),
        encoding="utf-8",
    )

    audit = audit_logs([log])

    assert audit.transcript_violations == 1
    assert audit.pcm_violations == 1
    assert not audit.privacy_safe
