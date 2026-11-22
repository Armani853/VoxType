from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt

from voxtype.ui.main_window import MainWindow
from voxtype.ui.models import AppStatus, DictationLanguage, ModelProfile, UIState


class _Controller:
    def __init__(self) -> None:
        self.toggles = 0
        self.language_switches = 0

    def toggle_dictation(self) -> None:
        self.toggles += 1

    def toggle_pause(self) -> None:
        return None

    def switch_language(self) -> None:
        self.language_switches += 1


def test_dashboard_viewport_reports_first_paint(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    paint = QEvent(QEvent.Type.Paint)
    window.eventFilter(window.centralWidget(), paint)
    assert not window._first_content_paint_seen
    window.eventFilter(window._dashboard_scroll.viewport(), paint)
    assert window._first_content_paint_seen


def test_confirmed_deltas_preserve_edits_through_finalization(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    machine_text = "Первая фраза. Вторая фраза. Последняя фраза."
    window.set_live_text_source(lambda _session, offset, limit: machine_text[offset:limit])
    active = SimpleNamespace(
        state=AppStatus.LISTENING,
        live_session_id="stream",
        live_committed_length=len("Первая фраза."),
    )
    window.update_snapshot(active)
    assert window.result_text() == "Первая фраза."
    window.result_editor.setPlainText("Моя правка.")
    window.result_editor.document().setModified(True)
    active.live_committed_length += len(" Вторая фраза.")
    window.update_snapshot(active)
    assert window.result_text() == "Моя правка. Вторая фраза."
    window.update_snapshot(
        SimpleNamespace(state=AppStatus.COMPLETED, full_transcript=machine_text, live_final=True)
    )
    assert window.result_text() == "Моя правка. Вторая фраза. Последняя фраза."


def test_main_window_renders_snapshot_and_routes_commands(qtbot) -> None:  # type: ignore[no-untyped-def]
    controller = _Controller()
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    window.update_snapshot(
        UIState(
            state=AppStatus.LISTENING,
            language=DictationLanguage.RU,
            session_elapsed_s=65,
            queued_audio_seconds=2.25,
            last_committed_text="Последний распознанный фрагмент",
            target_title="Блокнот",
            input_rms=0.45,
            microphone_name="USB Microphone",
            model_name="small",
            model_profile=ModelProfile.FAST,
        )
    )

    assert window.status_label.text() == "Слушаю"
    assert window.voice_action_label.text() == "Остановить"
    assert window.duration_tile.value_label.text() == "00:01:05"
    assert window.backlog_tile.value_label.text() == "2.2 с"
    assert window.target_tile.value_label.text() == "Блокнот"
    assert window.fragment_label.text() == "Последний распознанный фрагмент"
    assert window.microphone_label.text() == "USB Microphone"
    assert window.model_title.text() == "Режим"
    assert window.model_label.text() == "Быстро"
    assert window.profile_label.text() == "Локальная модель: small"
    assert window.audio_meter.level > 0

    qtbot.mouseClick(window.dictation_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.language_control.en_button, Qt.MouseButton.LeftButton)
    assert controller.toggles == 1
    assert controller.language_switches == 1


def test_main_window_uses_state_text_without_showing_provisional_as_final(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.update_snapshot(
        UIState(
            state=AppStatus.LISTENING,
            provisional_text="Это ещё не окончательный текст",
            input_rms=10 ** (-64.4 / 20),
        )
    )
    assert window.fragment_label.text() == "Слушаю…"
    assert window.audio_meter.level > 0
    window.update_snapshot(UIState(state=AppStatus.TRANSCRIBING))
    assert window.fragment_label.text() == "Распознаю…"


def test_main_window_visual_state_matrix_is_complete(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)

    for status in AppStatus:
        window.update_snapshot(
            UIState(
                state=status,
                error_message="Проверяемое предупреждение"
                if status in {AppStatus.ERROR_RECOVERABLE, AppStatus.ERROR_FATAL}
                else "",
                full_transcript="Сохранённый результат" if status is AppStatus.COMPLETED else "",
            )
        )
        assert window.status_label.text()
        assert window.status_detail_label.text()


def test_main_window_minimizes_to_tray_and_can_force_close(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(minimize_to_tray=True)
    qtbot.addWidget(window)
    window.show()
    with qtbot.waitSignal(window.hidden_to_tray, timeout=1000):
        window.close()
    assert window.isHidden()
    window.force_close()


def test_main_window_close_requests_application_exit_without_tray(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(minimize_to_tray=False)
    qtbot.addWidget(window)
    window.show()
    with qtbot.waitSignal(window.exit_requested, timeout=1000):
        window.close()
    assert window.isVisible()
    window.force_close()


def test_main_window_error_keeps_copy_action_available(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(minimize_to_tray=False)
    qtbot.addWidget(window)
    window.update_snapshot(
        UIState(
            state=AppStatus.ERROR_RECOVERABLE,
            error_message="Микрофон отключён",
            last_committed_text="Сохранённый текст",
        )
    )
    assert not window.error_banner.isHidden()
    assert window.error_label.text() == "Микрофон отключён"
    assert window.copy_text_button.isEnabled()


def test_main_window_completed_state_shows_full_transcript_and_new_start(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window.update_snapshot(
        UIState(
            state=AppStatus.COMPLETED,
            last_committed_text="последний",
            full_transcript="весь завершённый текст",
            delivery_status="delivered",
        )
    )
    assert window.status_label.text() == "Готово"
    assert window.fragment_label.text() == "весь завершённый текст"
    assert window.voice_action_label.text() == "Начать диктовку"
    assert window.dictation_button.isEnabled()


def test_completed_text_waiting_for_target_has_clear_recovery_instruction(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window.update_snapshot(
        UIState(
            state=AppStatus.COMPLETED,
            full_transcript="сохранённый текст",
            status_message="Готово",
            delivery_status="queued_target_inactive",
        )
    )
    assert window.status_label.text() == "Ожидает вставки"
    assert "Повторить вставку" in window.status_detail_label.text()


def test_missing_model_is_not_presented_as_ready_and_opens_setup(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_model_available(False)
    window.update_snapshot(UIState(state=AppStatus.IDLE, model_name="small"))
    assert window.status_label.text() == "Требуется модель"
    assert "не установлена" in window.profile_label.text()
    assert window.voice_action_label.text() == "Настроить модель"
    with qtbot.waitSignal(window.onboarding_requested):
        qtbot.mouseClick(window.dictation_button, Qt.MouseButton.LeftButton)

    window.set_model_available(True)
    assert window.status_label.text() == "Готово"
    assert window.voice_action_label.text() == "Начать диктовку"
