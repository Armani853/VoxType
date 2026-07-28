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
    assert values["dictation"]["model_profile"] == "balanced"
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
    assert window.selected_session_id() == "two"
    assert window.detail_session_id == "two"
    assert window.visible_session_ids == {"two"}
    assert window.text_edit.toPlainText() == "English text"
    assert "EN" in window.metadata_label.text()
    assert "Notepad" in window.metadata_label.text()

    window.search_edit.setText("no matching transcript")
    assert window.visible_session_ids == set()
    assert window.detail_session_id is None
    assert window.text_edit.toPlainText() == ""
    assert window.metadata_label.text() == "Выберите запись в истории"
    assert window.empty_label.text() == "Записей не найдено"


def test_history_language_filter_never_keeps_stale_detail_and_delete_selects_next(
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    window = HistoryWindow()
    qtbot.addWidget(window)
    window.resize(1200, 650)
    window.set_sessions(
        [
            HistoryEntry(
                session_id="ru",
                started_at=datetime(2026, 7, 22, 10, 0),
                language="ru",  # type: ignore[arg-type]
                confirmed_text="Русский текст",
                target_application="Блокнот",
            ),
            HistoryEntry(
                session_id="en-new",
                started_at=datetime(2026, 7, 21, 10, 0),
                language="en",  # type: ignore[arg-type]
                confirmed_text="New English",
                target_application="Browser",
                completion_status="recovered",
            ),
            HistoryEntry(
                session_id="en-old",
                started_at=datetime(2026, 7, 20, 10, 0),
                language="en",  # type: ignore[arg-type]
                confirmed_text="Old English",
                target_application="Notepad",
            ),
        ]
    )
    window.language_filter.setCurrentIndex(window.language_filter.findData("en"))
    assert window.selected_session_id() == "en-new"
    assert window.detail_session_id == "en-new"
    assert window.detail_session_id in window.visible_session_ids
    assert "EN" in window.metadata_label.text()
    assert "Восстановлено" in window.metadata_label.text()

    window.remove_session("en-new")
    assert window.selected_session_id() == "en-old"
    assert window.detail_session_id == "en-old"
    assert window.text_edit.toPlainText() == "Old English"
    assert window.detail_session_id in window.visible_session_ids

    window.show()
    QGuiApplication.processEvents()
    assert window.table.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert not window.table.horizontalScrollBar().isVisible()


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
