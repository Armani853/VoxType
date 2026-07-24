from datetime import datetime

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialogButtonBox

from voxtype.ui.diagnostics_window import DiagnosticsWindow
from voxtype.ui.history_window import HistoryWindow
from voxtype.ui.models import DiagnosticReport, HistoryEntry
from voxtype.ui.settings_window import SettingsWindow


def test_settings_roundtrip_and_submit(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = SettingsWindow()
    qtbot.addWidget(window)
    window.set_audio_devices([("USB Mic", "mic-1")], "mic-1")
    window.set_models([("Whisper small", "small")], "small")
    window.set_values(
        {
            "general": {"theme": "dark", "show_overlay": False},
            "dictation": {
                "default_language": "en",
                "model_profile": "fast",
                "custom_vocabulary": ["PySide6", "Иван Петров"],
            },
            "audio": {"device_id": "mic-1", "vad_sensitivity": 0.7},
            "model": {"name": "small", "cpu_threads": 6},
            "privacy": {"save_history": True, "retention_days": 14},
            "hotkeys": {"toggle": "Ctrl+Alt+Space"},
        }
    )
    values = window.values()
    assert values["general"]["theme"] == "dark"
    assert values["dictation"]["language"] == "en"
    assert values["audio"]["device_id"] == "mic-1"
    assert values["audio"]["vad_sensitivity"] == 0.7
    assert values["model"]["cpu_threads"] == 6
    assert values["dictation"]["custom_vocabulary"] == ["PySide6", "Иван Петров"]

    window.overlay_check.setChecked(True)
    apply_button = window.button_box.button(QDialogButtonBox.StandardButton.Apply)
    with qtbot.waitSignal(window.settings_submitted, timeout=1000) as signal:
        qtbot.mouseClick(apply_button, Qt.MouseButton.LeftButton)
    assert signal.args[0]["general"]["show_overlay"] is True


def test_settings_accepts_slots_based_application_schema(qtbot) -> None:  # type: ignore[no-untyped-def]
    from voxtype.settings.schema import AppSettings

    window = SettingsWindow()
    qtbot.addWidget(window)
    window.set_audio_devices([("Default", 3)])
    window.set_models([("small", "small")])
    settings = AppSettings()
    settings.general.start_with_windows = True
    settings.audio.device_id = 3
    settings.model.installed_model = "small"
    window.set_values(settings)

    values = window.values()
    assert values["general"]["start_with_windows"] is True
    assert values["general"]["ui_language"] == "ru"
    assert values["dictation"]["target_mode"] == "locked"
    assert values["dictation"]["model_profile"] == "fast"
    assert values["audio"]["device_id"] == 3
    assert values["model"]["installed_model"] == "small"


def test_history_filters_displays_and_copies(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = HistoryWindow()
    qtbot.addWidget(window)
    window.set_sessions(
        [
            HistoryEntry(
                session_id="one",
                started_at=datetime(2026, 7, 21, 10, 0),
                language="ru",  # type: ignore[arg-type]
                confirmed_text="Русский текст",
                target_application="Блокнот",
                duration_s=61,
            ),
            HistoryEntry(
                session_id="two",
                started_at=datetime(2026, 7, 20, 9, 0),
                language="en",  # type: ignore[arg-type]
                confirmed_text="English text",
                target_application="Notepad",
                duration_s=30,
            ),
        ]
    )
    assert window.table.rowCount() == 2
    assert window.selected_entry() is not None
    qtbot.mouseClick(window.copy_button, Qt.MouseButton.LeftButton)
    assert QGuiApplication.clipboard().text() == "Русский текст"

    window.search_edit.setText("English")
    hidden = [window.table.isRowHidden(row) for row in range(window.table.rowCount())]
    assert hidden.count(False) == 1


def test_diagnostics_renders_and_copies_report(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = DiagnosticsWindow()
    qtbot.addWidget(window)
    window.set_report(
        DiagnosticReport(
            summary="Все основные проверки пройдены",
            hardware={"CPU": "Test CPU", "RAM": "24 GB"},
            audio={"Микрофон": "USB Mic"},
            libraries={"PySide6": "6.11"},
            checks=(("Модель", True, "Готова"),),
        )
    )
    assert window.summary_label.text() == "Все основные проверки пройдены"
    assert window.checks_table.rowCount() == 1
    assert window._tables["hardware"].rowCount() == 2
    qtbot.mouseClick(window.copy_button, Qt.MouseButton.LeftButton)
    copied = QGuiApplication.clipboard().text()
    assert "Test CPU" in copied
    assert "Модель" in copied
