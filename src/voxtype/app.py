"""PySide6 desktop application shell for VoxType."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from voxtype.asr.base import TranscriptionRequest
from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS
from voxtype.asr.profiles import ASRProfile
from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.devices import choose_capture_rate, list_input_devices
from voxtype.audio.preprocessing import (
    PreprocessingProfile as AudioPreprocessingProfile,
)
from voxtype.audio.preprocessing import PreprocessorSettings
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.monitoring.hardware import probe_hardware
from voxtype.monitoring.health import run_health_checks
from voxtype.runtime import RuntimeBundle, build_runtime, domain_profile, install_silero_vad
from voxtype.ui import (
    DiagnosticsWindow,
    DictationOverlay,
    HistoryWindow,
    MainWindow,
    OnboardingWizard,
    SettingsWindow,
    ThemeMode,
    TrayController,
    apply_theme,
    create_app_icon,
)
from voxtype.version import __version__
from voxtype.windows.hotkeys import GlobalHotkeyManager, parse_hotkey
from voxtype.windows.send_input import SendInputController
from voxtype.windows.target_window import TargetPolicy

_LOGGER = logging.getLogger("voxtype.app")


class _WorkerSignals(QObject):
    model_progress = Signal(int, str)
    model_ready = Signal(bool, str, str)
    calibration_ready = Signal(bool, str)
    phrase_ready = Signal(str, str, bool)
    diagnostics_ready = Signal(object)


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, manager: GlobalHotkeyManager) -> None:
        super().__init__()
        self.manager = manager

    def nativeEventFilter(
        self,
        event_type: QByteArray | bytes | bytearray | memoryview[int],
        message: int,
    ) -> tuple[bool, int]:
        event_bytes = event_type.data() if isinstance(event_type, QByteArray) else bytes(event_type)
        if b"windows" not in event_bytes:
            return False, 0
        try:
            from ctypes import wintypes

            native = wintypes.MSG.from_address(int(message))
            handled = self.manager.handle_message(
                int(native.message), int(native.wParam), int(native.lParam)
            )
        except (OSError, TypeError, ValueError):
            return False, 0
        return handled is not None, 0


class DesktopApplication:
    """Own Qt views and adapt them to the Qt-independent controller."""

    def __init__(self, application: QApplication, runtime: RuntimeBundle) -> None:
        self.application = application
        self.runtime = runtime
        self.controller = runtime.controller
        self.signals = _WorkerSignals()
        self.main = MainWindow(
            self.controller,
            interface_language=runtime.settings.general.ui_language,
            minimize_to_tray=runtime.settings.general.minimize_to_tray,
        )
        self.overlay = DictationOverlay()
        self.tray = TrayController(self.controller)
        self.settings_window = SettingsWindow(self.main)
        self.history_window = HistoryWindow(self.main)
        self.diagnostics_window = DiagnosticsWindow(self.main)
        self.onboarding: OnboardingWizard | None = None
        self.hotkeys = GlobalHotkeyManager(hwnd=0)
        self.hotkey_filter = _HotkeyEventFilter(self.hotkeys)
        self.timer = QTimer(self.main)
        self.timer.setInterval(100)
        self._last_state = ""
        self._closed = False
        self._connect()
        self._configure_hotkeys()
        self.application.installNativeEventFilter(self.hotkey_filter)
        apply_theme(application, ThemeMode(runtime.settings.general.theme))
        self.application.setWindowIcon(create_app_icon())
        self.application.setQuitOnLastWindowClosed(False)
        self.main.show()
        self.controller.target_manager.register_own_window(int(self.main.winId()))
        self.tray.show()
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        if not runtime.settings.general.onboarding_completed:
            QTimer.singleShot(100, self.show_onboarding)

    def _connect(self) -> None:
        self.main.settings_requested.connect(self.show_settings)
        self.main.history_requested.connect(self.show_history)
        self.main.diagnostics_requested.connect(self.show_diagnostics)
        self.main.onboarding_requested.connect(self.show_onboarding)
        self.main.retry_requested.connect(self.controller.toggle_dictation)
        self.main.copy_text_button.clicked.connect(self.controller.copy_pending_text)
        self.tray.open_window_requested.connect(self._show_main)
        self.tray.settings_requested.connect(self.show_settings)
        self.tray.quit_requested.connect(self.quit)
        self.settings_window.settings_submitted.connect(self._save_settings)
        self.settings_window.onboarding_requested.connect(self.show_onboarding)
        self.settings_window.open_data_folder_requested.connect(self._open_data_folder)
        self.settings_window.model_download_requested.connect(self._download_model)
        self.settings_window.model_delete_requested.connect(self._delete_model)
        self.settings_window.self_test_requested.connect(self.show_diagnostics)
        self.history_window.refresh_requested.connect(self._refresh_history)
        self.diagnostics_window.refresh_requested.connect(self._refresh_diagnostics)
        self.diagnostics_window.self_test_requested.connect(self._refresh_diagnostics)
        self.signals.model_progress.connect(self._on_model_progress)
        self.signals.model_ready.connect(self._on_model_ready)
        self.signals.calibration_ready.connect(self._on_calibration_ready)
        self.signals.phrase_ready.connect(self._on_phrase_ready)
        self.signals.diagnostics_ready.connect(self._on_diagnostics_ready)
        self.application.aboutToQuit.connect(self.close)

    def _show_main(self) -> None:
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def _poll(self) -> None:
        snapshot = self.controller.snapshot()
        self.main.update_snapshot(snapshot)
        self.tray.update_snapshot(snapshot)
        self.overlay.update_snapshot(snapshot)
        active = snapshot.state in {"LISTENING", "SPEECH_DETECTED", "TRANSCRIBING"}
        if active and self.runtime.settings.general.show_overlay:
            self.overlay.show_without_activating()
        elif not active:
            self.overlay.hide()
        if snapshot.state != self._last_state and snapshot.state == "ERROR_RECOVERABLE":
            self.tray.show_message(
                "VoxType", snapshot.error_message or snapshot.status_message, critical=True
            )
        self._last_state = snapshot.state

    def _configure_hotkeys(self) -> None:
        self.hotkeys.unregister_all()
        definitions: tuple[tuple[str, int, str, Any], ...] = (
            (
                self.runtime.settings.hotkeys.toggle,
                1,
                "toggle_dictation",
                self.controller.toggle_dictation,
            ),
            (
                self.runtime.settings.hotkeys.language,
                2,
                "toggle_language",
                self.controller.switch_language,
            ),
            (self.runtime.settings.hotkeys.pause, 3, "toggle_pause", self.controller.toggle_pause),
        )
        try:
            for specification, identifier, name, callback in definitions:
                hotkey = parse_hotkey(specification, identifier=identifier, name=name)

                def invoke(_item: object, fn: Any = callback) -> None:
                    fn()

                self.hotkeys.register(hotkey, invoke)
        except (ValueError, OSError, RuntimeError) as exc:
            self.hotkeys.unregister_all()
            self.tray.show_message("Горячая клавиша недоступна", str(exc), critical=True)

    def show_settings(self) -> None:
        self.settings_window.set_values(self.runtime.settings)
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in list_input_devices()]
        self.settings_window.set_audio_devices(devices, self.runtime.settings.audio.device_id)
        models = [
            (item.model_id, item.model_id) for item in self.runtime.model_manager.list_installed()
        ]
        if not models:
            models = [("Модель ещё не установлена", self.runtime.settings.model.installed_model)]
        self.settings_window.set_models(models, self.runtime.settings.model.installed_model)
        self.settings_window.show()
        self.settings_window.raise_()

    def _save_settings(self, values: object) -> None:
        if not isinstance(values, dict):
            return
        previous_autostart = self.runtime.settings.general.start_with_windows
        updated = self.runtime.settings_repository.update(values)
        self.runtime.settings = updated
        self.controller.settings = updated
        self.controller.target_manager.set_policy(TargetPolicy(updated.dictation.target_mode.value))
        self.controller.injection_service.strategy = updated.dictation.injection_mode.value
        self.main.set_minimize_to_tray(updated.general.minimize_to_tray)
        self.main.set_interface_language(updated.general.ui_language)
        apply_theme(self.application, ThemeMode(updated.general.theme))
        if updated.general.start_with_windows != previous_autostart:
            self.runtime.startup.set_enabled(updated.general.start_with_windows)
        self._configure_capture_factory()
        self._configure_hotkeys()

    def _configure_capture_factory(self) -> None:
        devices = list_input_devices()
        device = next(
            (item for item in devices if item.index == self.runtime.settings.audio.device_id),
            self.runtime.device,
        )
        rate = choose_capture_rate(device)
        profile = AudioPreprocessingProfile(self.runtime.settings.audio.preprocessing_profile.value)
        processing = replace(
            PreprocessorSettings.for_profile(profile),
            normalization_enabled=self.runtime.settings.audio.normalization,
            noise_suppression_enabled=self.runtime.settings.audio.noise_suppression,
        )

        def factory(queue: LosslessAudioQueue) -> AudioCaptureService:
            return AudioCaptureService(
                queue,
                AudioCaptureConfig(device=device.index, input_sample_rate=rate),
                preprocessing=processing,
            )

        self.controller.capture_factory = factory
        self.controller.microphone_name = f"{device.name} ({device.host_api})"
        self.runtime.device = device

    def show_history(self) -> None:
        self._refresh_history()
        self.history_window.show()
        self.history_window.raise_()

    def _refresh_history(self) -> None:
        self.history_window.set_sessions(list(self.runtime.session_repository.recent(250)))

    def show_diagnostics(self) -> None:
        self.diagnostics_window.show()
        self.diagnostics_window.raise_()
        self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        self.diagnostics_window.set_self_test_running(True, "Выполняю локальные проверки…")

        def worker() -> None:
            try:
                hardware = probe_hardware()
                checks = run_health_checks(self.runtime.data_directory)
                libraries: dict[str, str] = {"VoxType": __version__}
                for name in ("PySide6", "faster-whisper", "ctranslate2", "onnxruntime", "numpy"):
                    try:
                        libraries[name] = importlib.metadata.version(name)
                    except importlib.metadata.PackageNotFoundError:
                        libraries[name] = "не установлена"
                report = {
                    "summary": "Все обязательные проверки пройдены"
                    if all(item.ok for item in checks)
                    else "Есть замечания",
                    "hardware": {
                        "ОС": hardware.os_name,
                        "CPU": hardware.cpu_name,
                        "Ядра": f"{hardware.physical_cores}/{hardware.logical_cores}",
                        "RAM": f"{hardware.total_ram_bytes / 1024**3:.1f} ГБ",
                        "GPU": ", ".join(hardware.gpu_names) or "не обнаружен",
                        "CUDA": "да" if hardware.cuda_available else "нет",
                        "Vulkan": "да" if hardware.vulkan_available else "нет",
                    },
                    "audio": {
                        "Микрофон": self.controller.microphone_name,
                        "Частоты": ", ".join(map(str, hardware.supported_sample_rates)),
                    },
                    "runtime": {
                        key: str(value)
                        for key, value in self.controller.metrics.snapshot().items()
                        if key != "samples"
                    },
                    "libraries": libraries,
                    "checks": tuple((item.name, item.ok, item.detail) for item in checks),
                    "log_excerpt": _tail(self.runtime.log_path, 80),
                }
            except Exception as exc:
                report = {
                    "summary": f"Диагностика не завершена: {type(exc).__name__}",
                    "checks": (),
                }
            self.signals.diagnostics_ready.emit(report)

        threading.Thread(target=worker, name="voxtype-diagnostics", daemon=True).start()

    def _on_diagnostics_ready(self, report: object) -> None:
        self.diagnostics_window.set_self_test_running(False)
        self.diagnostics_window.set_report(report)

    def show_onboarding(self) -> None:
        if self.onboarding is not None and self.onboarding.isVisible():
            self.onboarding.raise_()
            return
        wizard = OnboardingWizard(self.main)
        self.onboarding = wizard
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in list_input_devices()]
        wizard.set_microphones(devices, self.runtime.device.index)
        wizard.microphone_refresh_requested.connect(lambda: wizard.set_microphones(devices))
        wizard.calibration_requested.connect(self._calibrate)
        wizard.hardware_probe_requested.connect(lambda: self._recommend_hardware(wizard))
        wizard.model_download_requested.connect(self._download_model)
        wizard.phrase_test_requested.connect(self._test_phrase)
        wizard.insertion_test_requested.connect(self._test_insertion)
        wizard.setup_completed.connect(self._complete_onboarding)
        wizard.show()

    def _recommend_hardware(self, wizard: OnboardingWizard) -> None:
        hardware = probe_hardware()
        profile = "balanced" if hardware.total_ram_bytes >= 16 * 1024**3 else "fast"
        summary = (
            f"{hardware.cpu_name}; {hardware.total_ram_bytes / 1024**3:.1f} ГБ RAM. "
            "Окончательный выбор можно изменить после локального benchmark."
        )
        wizard.set_hardware_recommendation(profile, summary)

    def _calibrate(self, device_text: str) -> None:
        try:
            device = int(device_text)
        except ValueError:
            device = self.runtime.device.index

        def worker() -> None:
            try:
                import sounddevice as sd

                audio = sd.rec(
                    48_000 * 3, samplerate=48_000, channels=1, dtype="float32", device=device
                )
                sd.wait()
                rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
                ok = 0.001 < rms < 0.8
                message = f"Средний уровень {20 * np.log10(max(rms, 1e-9)):.1f} dBFS — " + (
                    "норма" if ok else "проверьте микрофон"
                )
            except Exception as exc:
                ok, message = False, f"Калибровка не завершена: {type(exc).__name__}"
            self.signals.calibration_ready.emit(ok, message)

        threading.Thread(target=worker, name="voxtype-calibration", daemon=True).start()

    def _on_calibration_ready(self, ok: bool, message: str) -> None:
        if self.onboarding is not None:
            self.onboarding.set_calibration_result(ok, message)

    def _download_model(self, profile_text: str) -> None:
        selected = (
            ASRProfile.MAXIMUM_ACCURACY if profile_text == "accurate" else ASRProfile(profile_text)
        )
        spec = DEFAULT_MODEL_SPECS[selected]

        def worker() -> None:
            try:

                def progress(done: int, total: int | None) -> None:
                    percent = int(done * 100 / total) if total else 0
                    self.signals.model_progress.emit(percent, f"{done / 1024**2:.0f} МБ")

                self.runtime.model_manager.install(spec, progress=progress)
                install_silero_vad(self.runtime.data_directory)
                backend = self.runtime.backend_for(selected)
                configured = self.controller.configure_backend(backend, domain_profile(selected))
                if configured:
                    self.runtime.settings = self.runtime.settings_repository.load()
                self.signals.model_ready.emit(
                    configured,
                    "Модель установлена и проверена"
                    if configured
                    else "Остановите диктовку и повторите",
                    spec.model_id,
                )
            except Exception as exc:
                _LOGGER.exception("model_install_failed type=%s", type(exc).__name__)
                self.signals.model_ready.emit(
                    False, f"Установка не завершена: {type(exc).__name__}", spec.model_id
                )

        threading.Thread(target=worker, name="voxtype-model-install", daemon=True).start()

    def _on_model_progress(self, percent: int, message: str) -> None:
        self.settings_window.set_model_download_progress(percent, message)
        if self.onboarding is not None:
            self.onboarding.set_model_progress(percent, message)

    def _on_model_ready(self, ready: bool, message: str, model_id: str) -> None:
        self.settings_window.set_diagnostics_status(message, ready)
        if ready:
            self.settings_window.set_models([(model_id, model_id)], model_id)
        if self.onboarding is not None:
            self.onboarding.set_model_ready(ready, message)

    def _delete_model(self, model_id: str) -> None:
        if self.controller.state.name != "IDLE" or model_id == self.controller.backend.model_id:
            self.settings_window.set_diagnostics_status("Нельзя удалить активную модель", False)
            return
        removed = self.runtime.model_manager.remove(model_id)
        self.settings_window.set_diagnostics_status(
            "Модель удалена" if removed else "Модель не найдена", removed
        )

    def _test_phrase(self, language: str) -> None:
        def worker() -> None:
            try:
                import sounddevice as sd

                rate = choose_capture_rate(self.runtime.device)
                recorded = sd.rec(
                    rate * 6,
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    device=self.runtime.device.index,
                )
                sd.wait()
                mono: np.ndarray[Any, np.dtype[np.float32]] = np.asarray(
                    recorded, dtype=np.float32
                ).reshape(-1)
                if rate != 16_000:
                    from scipy.signal import resample_poly

                    mono = np.asarray(resample_poly(mono, 16_000, rate), dtype=np.float32)
                language_code = cast(Literal["ru", "en"], language)
                result = self.controller.backend.transcribe(
                    TranscriptionRequest(mono, 16_000, language_code)
                )
                text = result.text
                ok = bool(text.strip())
            except Exception as exc:
                text, ok = f"{type(exc).__name__}", False
            self.signals.phrase_ready.emit(language, text, ok)

        threading.Thread(target=worker, name="voxtype-phrase-test", daemon=True).start()

    def _on_phrase_ready(self, language: str, text: str, ok: bool) -> None:
        if self.onboarding is not None:
            self.onboarding.set_phrase_result(language, text, ok)

    def _test_insertion(self, text: str) -> None:
        QTimer.singleShot(120, lambda: SendInputController().send_unicode(text))

    def _complete_onboarding(self, values: object) -> None:
        if not isinstance(values, dict):
            return
        changes = {
            "general": {"onboarding_completed": True},
            "audio": {"device_id": values.get("audio_device_id")},
            "dictation": {
                "model_profile": "maximum_accuracy"
                if values.get("model_profile") == "accurate"
                else values.get("model_profile", "balanced")
            },
            "hotkeys": {"toggle": values.get("toggle_hotkey", "Ctrl+Alt+Space")},
        }
        self._save_settings(changes)

    def _open_data_folder(self) -> None:
        os.startfile(self.runtime.data_directory)

    def quit(self) -> None:
        self.close()
        self.application.quit()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self.hotkeys.close()
        self.overlay.close()
        self.tray.close()
        self.main.force_close()
        self.runtime.close()


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def run_desktop(runtime: RuntimeBundle | None = None) -> int:
    existing = QApplication.instance()
    application = cast(QApplication, existing) if existing is not None else QApplication([])
    application.setApplicationName("VoxType")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("VoxType")
    shell = DesktopApplication(application, runtime or build_runtime())
    # Keep a Python reference for the full Qt event loop lifetime.
    application.setProperty("voxtype_shell", shell)
    return int(application.exec())


__all__ = ["DesktopApplication", "run_desktop"]
