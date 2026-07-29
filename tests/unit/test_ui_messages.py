from __future__ import annotations

import pytest

from voxtype.ui.messages import failure_message


@pytest.mark.parametrize(
    "operation",
    (
        "session_recovery",
        "diagnostics",
        "model_install",
        "benchmark",
        "microphone_test",
        "phrase_test",
    ),
)
def test_user_failure_message_explains_event_safety_and_next_action(operation: str) -> None:
    message = failure_message(operation)
    normalized = message.casefold()
    assert len(message.split(".")) >= 3
    assert any(word in normalized for word in ("сохранены", "не изменены"))
    assert any(word in normalized for word in ("повторите", "диагностику"))
    assert not any(
        technical in message
        for technical in ("Error", "Exception", "Traceback", "WinError", "HRESULT")
    )


def test_unknown_failure_context_is_a_programmer_error() -> None:
    with pytest.raises(ValueError, match="unknown user failure operation"):
        failure_message("unknown")
