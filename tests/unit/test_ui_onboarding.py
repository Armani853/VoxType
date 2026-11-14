import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from voxtype.audio.calibration import CalibrationStatus, analyze_calibration
from voxtype.ui.onboarding import OnboardingWizard


def test_onboarding_has_eight_real_steps_and_collects_choices(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    assert len(wizard.pageIds()) == 8

    wizard.set_microphones([("USB Microphone", "mic-1")], "mic-1")
    assert wizard.microphone_page.isComplete()
    assert wizard.microphone_combo.toolTip() == "USB Microphone"
    wizard.set_calibration_result(
        analyze_calibration(
            np.zeros(16_000, dtype=np.float32),
            np.full(16_000, 0.05, dtype=np.float32),
            sample_rate=16_000,
            speech_activity_confirmed=True,
        )
    )
    assert wizard.calibration_page.isComplete()
    assert wizard.calibration_progress.value() == 100
    assert wizard.calibration_button.property("primary") is False
    assert wizard.findChild(type(wizard.calibration_status), "onboardingStep1").text() == (
        "Шаг 1 из 8"
    )
    wizard.set_hardware_recommendation("balanced", "CPU и RAM подходят")
    assert wizard.hardware_page.isComplete()
    wizard.set_model_ready(True, "Модель готова")
    assert wizard.model_page.isComplete()
    wizard.set_phrase_result("ru", wizard.RU_TEST_PHRASE, True)
    wizard.set_phrase_result("en", wizard.EN_TEST_PHRASE, True)
    assert wizard.phrase_page.isComplete()
    wizard.set_insertion_test_text(wizard.INSERTION_TEST_TEXT)
    assert wizard.insertion_page.isComplete()

    values = wizard.setup_values()
    assert values["audio_device_id"] == "mic-1"
    assert values["model_profile"] == "balanced"
    assert values["onboarding_completed"] is True
    assert values["start_with_windows"] is True
    wizard.autostart_check.setChecked(False)
    assert wizard.setup_values()["start_with_windows"] is False


def test_onboarding_emits_external_work_requests(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    wizard.set_microphones([("Mic", "mic")])
    with qtbot.waitSignal(wizard.calibration_requested, timeout=1000) as signal:
        wizard.calibration_button.click()
    assert signal.args == ["mic"]

    with qtbot.waitSignal(wizard.model_download_requested, timeout=1000) as signal:
        wizard.download_button.click()
    assert signal.args == ["fast"]


def test_first_portaudio_device_index_zero_is_selectable(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    wizard.set_microphones([("Built-in microphone", 0)], 0)
    assert wizard.microphone_page.isComplete()
    assert wizard.microphone_status.text() == "Говорите обычным голосом"
    with qtbot.waitSignal(wizard.calibration_requested, timeout=1000) as signal:
        wizard.calibration_button.click()
    assert signal.args == ["0"]
    assert wizard.setup_values()["audio_device_id"] == 0


def test_device_refresh_does_not_start_microphone_test(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    selected: list[str] = []
    wizard.microphone_selected.connect(selected.append)

    wizard.set_microphones([("Built-in", 0), ("USB", 1)], 0)
    wizard.set_microphones([("Built-in", 0), ("USB", 1)], 0)
    assert selected == []

    wizard.microphone_combo.setCurrentIndex(1)
    assert selected == ["1"]


def test_completed_bad_calibration_stays_at_100_percent_with_separate_warning(
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    silence = np.zeros(16_000, dtype=np.float32)
    result = analyze_calibration(silence, silence, sample_rate=16_000)
    assert result.status is CalibrationStatus.NO_SPEECH_DETECTED
    wizard.set_calibration_result(result)
    assert wizard.calibration_progress.value() == 100
    assert not wizard.calibration_page.isComplete()
    assert not wizard.continue_anyway_button.isHidden()
    assert "Речь не обнаружена" in wizard.calibration_status.text()
