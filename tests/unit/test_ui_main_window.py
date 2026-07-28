import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

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


def test_main_window_renders_snapshot_and_routes_commands(qtbot) -> None:  # type: ignore[no-untyped-def]
    controller = _Controller()
    window = MainWindow(controller)
    qtbot.addWidget(window)
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
    assert window.dictation_button.text() == "Остановить"
    assert window.duration_tile.value_label.text() == "00:01:05"
    assert window.backlog_tile.value_label.text() == "2.2 с"
    assert window.target_tile.value_label.text() == "Блокнот"
    assert window.fragment_label.text() == "Последний распознанный фрагмент"
    assert window.microphone_label.text() == "USB Microphone"
    assert window.model_title.text() == "Режим"
    assert window.model_label.text() == "Быстро"
    assert window.profile_label.text() == "Локальная модель: small"
    assert window.audio_meter.level() > 0.5

    qtbot.mouseClick(window.dictation_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.language_control.en_button, Qt.MouseButton.LeftButton)
    assert controller.toggles == 1
    assert controller.language_switches == 1


def test_main_window_uses_state_text_without_showing_provisional_as_final(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow()
    qtbot.addWidget(window)
    window.update_snapshot(
        UIState(
            state=AppStatus.LISTENING,
            provisional_text="Это ещё не окончательный текст",
            input_rms=10 ** (-64.4 / 20),
        )
    )
    assert window.fragment_label.text() == "Слушаю…"
    assert window.audio_meter.level() > 0.1
    window.update_snapshot(UIState(state=AppStatus.TRANSCRIBING))
    assert window.fragment_label.text() == "Распознаю…"


def test_main_window_minimizes_to_tray_and_can_force_close(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(minimize_to_tray=True)
    qtbot.addWidget(window)
    window.show()
    with qtbot.waitSignal(window.hidden_to_tray, timeout=1000):
        window.close()
    assert window.isHidden()
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
