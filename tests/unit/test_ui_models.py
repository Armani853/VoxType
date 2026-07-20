from dataclasses import dataclass

from voxtype.ui.models import (
    AppStatus,
    DiagnosticReport,
    DictationLanguage,
    HistoryEntry,
    ModelProfile,
    UIState,
    diagnostic_report_from,
    format_backlog,
    format_duration,
    history_entry_from,
    snapshot_from,
    text_preview,
)


def test_snapshot_adapter_accepts_duck_typed_controller() -> None:
    @dataclass
    class ControllerSnapshot:
        state: str = "SPEECH_DETECTED"
        language: str = "en-US"
        session_elapsed_s: float = 3661.9
        queued_audio_seconds: float = 3.25
        last_committed_text: str = "hello"
        target_title: str = "Notepad"
        input_rms: float = 1.5

    state = snapshot_from(ControllerSnapshot())

    assert state.state is AppStatus.SPEECH_DETECTED
    assert state.language is DictationLanguage.EN
    assert state.input_rms == 1.0
    assert state.last_committed_text == "hello"
    assert format_duration(state.session_elapsed_s) == "01:01:01"
    assert format_backlog(state.queued_audio_seconds) == "3.2 с"


def test_ui_state_defensively_normalizes_invalid_values() -> None:
    state = UIState(
        state="unexpected-state",  # type: ignore[arg-type]
        language="something",  # type: ignore[arg-type]
        model_profile="unknown",  # type: ignore[arg-type]
        session_elapsed_s=-20,
        queued_audio_seconds=float("nan"),
        input_rms=-1,
        clipping_percentage=900,
    )

    assert state.state is AppStatus.ERROR_RECOVERABLE
    assert state.language is DictationLanguage.RU
    assert state.model_profile is ModelProfile.BALANCED
    assert state.session_elapsed_s == 0
    assert state.queued_audio_seconds == 0
    assert state.input_rms == 0
    assert state.clipping_percentage == 100
    preview = text_preview("word " * 300, 80)
    assert preview.startswith("…")
    assert len(preview) <= 80


def test_history_and_diagnostics_adapters() -> None:
    entry = history_entry_from(
        {
            "id": "session-1",
            "started_at": "2026-07-21T10:00:00",
            "language": "en",
            "text": "saved text",
            "duration": 12.5,
            "warnings": ["queue grew"],
        }
    )
    report = diagnostic_report_from(
        {
            "summary": "OK",
            "hardware": {"CPU": "Test CPU"},
            "checks": [("Модель", True, "Загружена")],
        }
    )

    assert isinstance(entry, HistoryEntry)
    assert entry.session_id == "session-1"
    assert entry.language is DictationLanguage.EN
    assert entry.confirmed_text == "saved text"
    assert isinstance(report, DiagnosticReport)
    assert report.hardware == {"CPU": "Test CPU"}
    assert report.checks[0][1] is True


def test_history_adapter_matches_session_repository_record() -> None:
    from voxtype.sessions.repository import SessionRecord

    record = SessionRecord(
        session_id="stored",
        started_at=1_750_000_000.0,
        finished_at=1_750_000_030.0,
        language="ru",
        model="small",
        profile="balanced",
        target_application="Блокнот",
        committed_text="Сохранённый текст",
        warnings=(),
        suspicious_fragments=(),
        audio_duration_s=30.0,
        processing_s=12.0,
        dropped_frames=0,
        status="completed",
    )

    entry = history_entry_from(record)
    assert entry.confirmed_text == "Сохранённый текст"
    assert entry.duration_s == 30.0
    assert entry.processing_time_s == 12.0
    assert entry.ended_at == 1_750_000_030.0
