import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from voxtype.ui.onboarding import OnboardingWizard


def test_onboarding_has_eight_real_steps_and_collects_choices(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    assert len(wizard.pageIds()) == 8

    wizard.set_microphones([("USB Microphone", "mic-1")], "mic-1")
    assert wizard.microphone_page.isComplete()
    wizard.set_calibration_result(True, "Уровень в норме")
    assert wizard.calibration_page.isComplete()
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


def test_onboarding_emits_external_work_requests(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard()
    qtbot.addWidget(wizard)
    wizard.set_microphones([("Mic", "mic")])
    with qtbot.waitSignal(wizard.calibration_requested, timeout=1000) as signal:
        wizard.calibration_button.click()
    assert signal.args == ["mic"]

    with qtbot.waitSignal(wizard.model_download_requested, timeout=1000) as signal:
        wizard.download_button.click()
    assert signal.args == ["balanced"]
