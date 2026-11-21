from uuid import uuid4

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt

from voxtype.ui.models import AppStatus, DictationLanguage, UIState
from voxtype.ui.overlay import DictationOverlay
from voxtype.ui.tray import TrayController


class _Controller:
    def __init__(self) -> None:
        self.toggle_calls = 0
        self.pause_calls = 0
        self.language_calls = 0
        self.shutdown_calls = 0

    def toggle_dictation(self) -> None:
        self.toggle_calls += 1

    def toggle_pause(self) -> None:
        self.pause_calls += 1

    def switch_language(self) -> None:
        self.language_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_overlay_is_nonactivating_and_has_both_modes(qtbot) -> None:  # type: ignore[no-untyped-def]
    settings = QSettings("VoxTypeTests", f"Overlay-{uuid4()}")
    settings.clear()
    controller = _Controller()
    overlay = DictationOverlay(controller, settings=settings)
    qtbot.addWidget(overlay)

    assert overlay.windowFlags() & Qt.WindowType.Tool
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    overlay.update_snapshot(
        UIState(
            state=AppStatus.TRANSCRIBING,
            language=DictationLanguage.EN,
            session_elapsed_s=12,
            queued_audio_seconds=4.2,
            provisional_text="provisional text",
            target_title="Notepad",
            input_rms=0.4,
        )
    )
    assert overlay.status_label.full_text() == "Обработка"
    assert overlay.language_label.text() == "EN"
    assert overlay.timer_label.text() == "00:00:12"
    assert overlay.processing_label.text() == "LOCAL"

    overlay.set_compact(False)
    assert not overlay.is_compact()
    assert overlay.expanded_panel.isVisible() is False  # parent is not shown yet
    overlay.show_without_activating()
    qtbot.waitUntil(overlay.isVisible)
    assert overlay.expanded_panel.isVisible()
    qtbot.mouseClick(overlay.pause_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(overlay.stop_button, Qt.MouseButton.LeftButton)
    assert controller.pause_calls == 1
    assert controller.toggle_calls == 1
    settings.clear()


def test_tray_menu_updates_and_routes_actions(qtbot) -> None:  # type: ignore[no-untyped-def]
    del qtbot
    controller = _Controller()
    tray = TrayController(controller)
    tray.update_snapshot(UIState(state=AppStatus.LISTENING, language=DictationLanguage.RU))
    assert tray.toggle_action.text() == "Остановить диктовку"
    assert tray.pause_action.isEnabled()
    assert tray.ru_action.isChecked()

    tray.toggle_action.trigger()
    tray.pause_action.trigger()
    tray.en_action.trigger()
    tray.quit_action.trigger()
    assert controller.toggle_calls == 1
    assert controller.pause_calls == 1
    assert controller.language_calls == 1
    assert controller.shutdown_calls == 1
    tray.close()


def test_completed_overlay_persists_full_result_and_exposes_actions(qtbot) -> None:  # type: ignore[no-untyped-def]
    overlay = DictationOverlay()
    qtbot.addWidget(overlay)
    overlay.update_snapshot(
        UIState(
            state=AppStatus.COMPLETED,
            last_committed_text="последний",
            full_transcript="полный подтверждённый результат",
            delivery_status="queued_target_inactive",
        )
    )
    overlay.show_without_activating()
    qtbot.waitUntil(overlay.isVisible)
    assert overlay.status_label.full_text() == "Ожидает вставки"
    assert overlay.result_text.toPlainText() == "полный подтверждённый результат"
    assert overlay.result_buttons.isVisible()
    assert overlay.copy_all_button.isEnabled()
    assert overlay.copy_all_button.accessibleName() == "Копировать результат"
    assert overlay.insert_button.isEnabled()
    assert (overlay.width(), overlay.height()) == (320, 100)
    assert overlay.provisional_label.full_text() == "полный подтверждённый результат"
    assert overlay.processing_label.full_text() == "Вернитесь в целевое окно"
    assert not overlay.pause_button.isVisible()
    assert not overlay.stop_button.isVisible()


def test_overlay_visual_state_matrix_is_complete(qtbot) -> None:  # type: ignore[no-untyped-def]
    overlay = DictationOverlay()
    qtbot.addWidget(overlay)
    overlay.set_compact(False, persist=False)

    for status in AppStatus:
        overlay.update_snapshot(
            UIState(
                state=status,
                error_message="Проверяемое предупреждение"
                if status in {AppStatus.ERROR_RECOVERABLE, AppStatus.ERROR_FATAL}
                else "",
                full_transcript="Сохранённый результат" if status is AppStatus.COMPLETED else "",
            )
        )
        assert overlay.status_label.full_text()
        assert overlay.processing_label.text()
