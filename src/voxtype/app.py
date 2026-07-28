"""PySide6 desktop application shell for VoxType."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import shutil
import sys
import threading
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

import numpy as np
from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from voxtype.asr.base import TranscriptionRequest
from voxtype.asr.benchmark import BenchmarkCase, benchmark_backend, save_benchmark_results
from voxtype.asr.model_manager import DEFAULT_MODEL_SPECS
from voxtype.asr.profiles import ASRProfile
from voxtype.audio.calibration import (
    CalibrationResult,
    analyze_calibration,
    device_error_result,
)
from voxtype.audio.capture import AudioCaptureConfig, AudioCaptureService
from voxtype.audio.devices import choose_capture_rate, list_input_devices
from voxtype.audio.preprocessing import (
    PreprocessingProfile as AudioPreprocessingProfile,
)
from voxtype.audio.preprocessing import PreprocessorSettings
from voxtype.audio.resampler import resample_audio
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.monitoring.hardware import probe_hardware
from voxtype.monitoring.health import run_health_checks
from voxtype.monitoring.logging import configure_logging
from voxtype.runtime import RuntimeBundle, build_runtime, domain_profile, install_silero_vad
from voxtype.settings.defaults import create_default_settings
from voxtype.transcript.vocabulary import Vocabulary
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
from voxtype.vad.segmenter import SegmentationSettings, StreamingSegmenter
from voxtype.vad.silero import SileroVAD
from voxtype.version import __version__
from voxtype.windows.hotkeys import GlobalHotkeyManager, parse_hotkey
from voxtype.windows.power_events import PowerEventCoordinator
from voxtype.windows.send_input import SendInputController
from voxtype.windows.target_window import TargetPolicy

_LOGGER = logging.getLogger("voxtype.app")


class _WorkerSignals(QObject):
    model_progress = Signal(int, str)
    model_ready = Signal(bool, str, str)
    calibration_progress = Signal(int, str)
    calibration_ready = Signal(object)
    phrase_ready = Signal(str, str, bool)
    diagnostics_ready = Signal(object)
    settings_status = Signal(str, object)
    audio_level = Signal(float, float, str)
    recovery_ready = Signal(str, bool, str)
    pending_flush_ready = Signal()
    hardware_ready = Signal(str, str)
    devices_ready = Signal(object)
    models_ready = Signal(object)
    shutdown_ready = Signal(bool)


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(
        self,
        manager: GlobalHotkeyManager,
        power_events: PowerEventCoordinator,
        shutdown_query: Any,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.power_events = power_events
        self.shutdown_query = shutdown_query

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
            if int(native.message) == 0x0011:  # WM_QUERYENDSESSION
                accepted = bool(self.shutdown_query())
                return True, 1 if accepted else 0
            if int(native.message) == 0x0016 and int(native.wParam):  # WM_ENDSESSION
                self.shutdown_query()
                return False, 0
            handled = self.manager.handle_message(
                int(native.message), int(native.wParam), int(native.lParam)
            )
            power = self.power_events.handle_message(
                int(native.message), int(native.wParam), int(native.lParam)
            )
        except (OSError, TypeError, ValueError):
            return False, 0
        return handled is not None or power is not None, 0


class DesktopApplication:
    """Own Qt views and adapt them to the Qt-independent controller."""

    def __init__(self, application: QApplication, runtime: RuntimeBundle) -> None:
        self.application = application
        self.runtime = runtime
        self.controller = runtime.controller
        self.signals = _WorkerSignals()
        self.main = MainWindow(
            None,
            interface_language=runtime.settings.general.ui_language,
            minimize_to_tray=runtime.settings.general.minimize_to_tray,
        )
        self.overlay = DictationOverlay(None)
        self.tray = TrayController(None)
        self.settings_window = SettingsWindow(self.main)
        self.history_window = HistoryWindow(self.main)
        self.diagnostics_window = DiagnosticsWindow(self.main)
        self.onboarding: OnboardingWizard | None = None
        self.hotkeys = GlobalHotkeyManager(hwnd=0)
        self._resume_after_suspend = False
        self.power_events = PowerEventCoordinator(
            on_suspend=self._on_suspend, on_resume=self._on_resume
        )
        self.hotkey_filter = _HotkeyEventFilter(self.hotkeys, self.power_events, self.close)
        self.timer = QTimer(self.main)
        self.timer.setInterval(50)
        self._last_state = ""
        self._result_dismissed = False
        self._last_pending_flush = 0.0
        self._last_resource_sample = 0.0
        self._pending_flush_running = False
        self._closed = False
        self._closing = False
        self._heartbeat_last = time.monotonic()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_timer = QTimer(self.main)
        self._heartbeat_timer.setInterval(100)
        self._heartbeat_timer.timeout.connect(self._heartbeat)
        if runtime.settings.general.start_with_windows:
            runtime.startup.enable()
        self._connect()
        self._configure_hotkeys()
        self.application.installNativeEventFilter(self.hotkey_filter)
        apply_theme(application, ThemeMode(runtime.settings.general.theme))
        self.application.setWindowIcon(create_app_icon())
        self.application.setQuitOnLastWindowClosed(False)
        self.main.show()
        self.main.set_toggle_hotkey(runtime.settings.hotkeys.toggle)
        self.controller.target_manager.register_own_window(int(self.main.winId()))
        self.tray.show()
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._heartbeat_timer.start()
        threading.Thread(
            target=self._heartbeat_monitor, name="voxtype-ui-watchdog", daemon=True
        ).start()
        if not runtime.settings.general.onboarding_completed:
            QTimer.singleShot(100, self.show_onboarding)
        if runtime.session_repository.unfinished():
            self.tray.show_message(
                "VoxType",
                "Найдена незавершённая сессия. Откройте историю для восстановления текста.",
            )
            QTimer.singleShot(250, self.show_history)

    def _connect(self) -> None:
        self.main.dictation_toggle_requested.connect(
            lambda: self._invoke_ui("main.toggle_dictation", self.controller.toggle_dictation)
        )
        self.main.pause_toggle_requested.connect(
            lambda: self._invoke_ui("main.toggle_pause", self.controller.toggle_pause)
        )
        self.main.language_change_requested.connect(
            lambda _language: self._invoke_ui(
                "main.switch_language", self.controller.switch_language
            )
        )
        self.overlay.pause_requested.connect(
            lambda: self._invoke_ui("overlay.toggle_pause", self.controller.toggle_pause)
        )
        self.overlay.stop_requested.connect(
            lambda: self._invoke_ui("overlay.stop", self.controller.toggle_dictation)
        )
        self.overlay.copy_all_requested.connect(
            lambda: self._run_background(
                "voxtype-copy-result", self.controller.copy_full_transcript
            )
        )
        self.overlay.insert_requested.connect(
            lambda: self._run_background(
                "voxtype-retry-insertion", self.controller.retry_completed_insertion
            )
        )
        self.overlay.close_result_requested.connect(self._dismiss_result)
        self.tray.dictation_toggle_requested.connect(
            lambda: self._invoke_ui("tray.toggle_dictation", self.controller.toggle_dictation)
        )
        self.tray.pause_toggle_requested.connect(
            lambda: self._invoke_ui("tray.toggle_pause", self.controller.toggle_pause)
        )
        self.tray.language_change_requested.connect(
            lambda _language: self._invoke_ui(
                "tray.switch_language", self.controller.switch_language
            )
        )
        self.main.settings_requested.connect(self.show_settings)
        self.main.history_requested.connect(self.show_history)
        self.main.diagnostics_requested.connect(self.show_diagnostics)
        self.main.onboarding_requested.connect(self.show_onboarding)
        self.main.retry_requested.connect(
            lambda: self._invoke_ui("main.retry", self.controller.toggle_dictation)
        )
        self.main.copy_text_button.clicked.connect(
            lambda: self._run_background(
                "voxtype-copy-full-result", self.controller.copy_full_transcript
            )
        )
        self.tray.open_window_requested.connect(self._show_main)
        self.tray.settings_requested.connect(self.show_settings)
        self.tray.quit_requested.connect(self.quit)
        self.settings_window.settings_submitted.connect(self._save_settings)
        self.settings_window.onboarding_requested.connect(self.show_onboarding)
        self.settings_window.open_data_folder_requested.connect(self._open_data_folder)
        self.settings_window.model_download_requested.connect(self._download_model)
        self.settings_window.model_delete_requested.connect(self._delete_model)
        self.settings_window.model_benchmark_requested.connect(self._benchmark_model)
        self.settings_window.audio_test_requested.connect(self._audio_test)
        self.settings_window.delete_local_data_requested.connect(self._delete_local_data)
        self.settings_window.open_log_requested.connect(self._open_log)
        self.settings_window.export_diagnostics_requested.connect(self._export_diagnostics)
        self.settings_window.show_versions_requested.connect(self.show_diagnostics)
        self.settings_window.self_test_requested.connect(self.show_diagnostics)
        self.history_window.refresh_requested.connect(self._refresh_history)
        self.history_window.delete_requested.connect(self._delete_history_session)
        self.history_window.recover_requested.connect(self._recover_history_session)
        self.history_window.export_requested.connect(self._export_history_session)
        self.diagnostics_window.refresh_requested.connect(self._refresh_diagnostics)
        self.diagnostics_window.self_test_requested.connect(self._refresh_diagnostics)
        self.diagnostics_window.open_log_requested.connect(self._open_log)
        self.diagnostics_window.export_requested.connect(self._export_diagnostics)
        self.signals.model_progress.connect(self._on_model_progress)
        self.signals.model_ready.connect(self._on_model_ready)
        self.signals.calibration_progress.connect(self._on_calibration_progress)
        self.signals.calibration_ready.connect(self._on_calibration_ready)
        self.signals.phrase_ready.connect(self._on_phrase_ready)
        self.signals.diagnostics_ready.connect(self._on_diagnostics_ready)
        self.signals.settings_status.connect(self.settings_window.set_diagnostics_status)
        self.signals.audio_level.connect(self.settings_window.update_microphone_level)
        self.signals.audio_level.connect(self._on_audio_level)
        self.signals.recovery_ready.connect(self._on_recovery_ready)
        self.signals.pending_flush_ready.connect(self._on_pending_flush_ready)
        self.signals.hardware_ready.connect(self._on_hardware_ready)
        self.signals.devices_ready.connect(self._on_devices_ready)
        self.signals.models_ready.connect(self._on_models_ready)
        self.signals.shutdown_ready.connect(self._on_shutdown_ready)
        self.application.aboutToQuit.connect(self.close)

    def _invoke_ui(self, handler: str, callback: Any) -> None:
        started = time.monotonic()
        state = self.controller.state.name
        try:
            callback()
        finally:
            elapsed_ms = (time.monotonic() - started) * 1_000.0
            if elapsed_ms > 200.0:
                _LOGGER.warning(
                    "UI_BLOCK handler=%s state=%s elapsed_ms=%.1f",
                    handler,
                    state,
                    elapsed_ms,
                )

    @staticmethod
    def _run_background(name: str, callback: Any) -> None:
        threading.Thread(target=callback, name=name, daemon=True).start()

    def _heartbeat(self) -> None:
        self._heartbeat_last = time.monotonic()

    def _heartbeat_monitor(self) -> None:
        reported = False
        while not self._heartbeat_stop.wait(0.1):
            elapsed = time.monotonic() - self._heartbeat_last
            if elapsed > 0.5 and not reported:
                _LOGGER.warning(
                    "UI_STALL state=%s elapsed_ms=%.1f",
                    self.controller.state.name,
                    elapsed * 1_000.0,
                )
                reported = True
            elif elapsed <= 0.25:
                reported = False

    def _show_main(self) -> None:
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def _dismiss_result(self) -> None:
        self._result_dismissed = True
        self.overlay.hide()

    def _poll(self) -> None:
        if self.controller.state.name == "IDLE":
            with suppress(OSError, RuntimeError):
                self.controller.target_manager.remember_foreground_target()
        snapshot = self.controller.snapshot()
        self.main.update_snapshot(snapshot)
        self.tray.update_snapshot(snapshot)
        self.overlay.update_snapshot(snapshot)
        active = snapshot.state in {
            "STARTING",
            "MODEL_LOADING",
            "LISTENING",
            "SPEECH_DETECTED",
            "TRANSCRIBING",
            "PAUSED_TARGET_LOST",
            "PAUSED",
            "FINISHING",
            "ERROR_RECOVERABLE",
            "ERROR_FATAL",
            "COMPLETED",
        }
        if snapshot.state != self._last_state and snapshot.state == "COMPLETED":
            self._result_dismissed = bool(
                self.runtime.settings.dictation.auto_hide_completed_result
            )
        elif snapshot.state in {"STARTING", "MODEL_LOADING", "LISTENING"}:
            self._result_dismissed = False
        if (
            active
            and self.runtime.settings.general.show_overlay
            and not (snapshot.state == "COMPLETED" and self._result_dismissed)
        ):
            self.overlay.show_without_activating()
        else:
            self.overlay.hide()
        if snapshot.state != self._last_state and snapshot.state == "ERROR_RECOVERABLE":
            self.tray.show_message(
                "VoxType", snapshot.error_message or snapshot.status_message, critical=True
            )
        if (
            snapshot.state != self._last_state
            and self.runtime.settings.general.play_sounds
            and snapshot.state in {"LISTENING", "IDLE", "ERROR_RECOVERABLE"}
        ):
            QApplication.beep()
        self._last_state = snapshot.state
        now = time.monotonic()
        if (
            snapshot.state == "PAUSED_TARGET_LOST"
            and self.controller.injection_service.pending_fragments
            and now - self._last_pending_flush >= 0.5
            and not self._pending_flush_running
        ):
            self._last_pending_flush = now
            self._pending_flush_running = True

            def flush_worker() -> None:
                try:
                    self.controller.flush_pending_text()
                finally:
                    self.signals.pending_flush_ready.emit()

            threading.Thread(target=flush_worker, name="voxtype-pending-flush", daemon=True).start()
        if now - self._last_resource_sample >= 10.0:
            self._last_resource_sample = now
            self.controller.sample_resources()

    def _on_suspend(self) -> None:
        self.controller.target_manager.on_suspend()
        if self.controller.state.name in {
            "LISTENING",
            "SPEECH_DETECTED",
            "TRANSCRIBING",
        } or (
            self.controller.state.name == "PAUSED_TARGET_LOST" and not self.controller.user_paused
        ):
            self._resume_after_suspend = self.controller.toggle_pause()

    def _on_resume(self) -> None:
        self.controller.target_manager.on_resume()
        if self._resume_after_suspend:
            self._resume_after_suspend = False
            self.controller.toggle_pause()

    def _configure_hotkeys(self) -> bool:
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

                def invoke(
                    _item: object,
                    fn: Any = callback,
                    handler_name: str = name,
                ) -> None:
                    self._invoke_ui(f"hotkey.{handler_name}", fn)

                self.hotkeys.register(hotkey, invoke)
        except (ValueError, OSError, RuntimeError) as exc:
            self.hotkeys.unregister_all()
            self.tray.show_message("Горячая клавиша недоступна", str(exc), critical=True)
            return False
        return True

    def show_settings(self) -> None:
        self.settings_window.set_values(self.runtime.settings)
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in self.runtime.devices]
        self.settings_window.set_audio_devices(devices, self.runtime.settings.audio.device_id)
        self._set_model_choices(set())
        self.settings_window.show()
        self.settings_window.raise_()
        self._run_background("voxtype-settings-devices", self._scan_settings_devices)
        self._run_background("voxtype-settings-models", self._scan_settings_models)

    def _set_model_choices(self, installed: set[str]) -> None:
        models = [
            (f"small — быстро/баланс{' ✓' if 'small' in installed else ''}", "small"),
            (
                f"large-v3-turbo — максимум{' ✓' if 'large-v3-turbo' in installed else ''}",
                "large-v3-turbo",
            ),
        ]
        self.settings_window.set_models(models, self.runtime.settings.model.installed_model)
        self.settings_window.gpu_experimental_check.setChecked(False)
        self.settings_window.gpu_experimental_check.setEnabled(False)
        self.settings_window.gpu_experimental_check.setToolTip(
            "В этой сборке используется проверенный CPU INT8; экспериментальный GPU отключён"
        )

    def _scan_settings_devices(self) -> None:
        self.signals.devices_ready.emit(list_input_devices())

    def _scan_settings_models(self) -> None:
        self.signals.models_ready.emit(
            {item.model_id for item in self.runtime.model_manager.list_installed()}
        )

    def _on_devices_ready(self, items: object) -> None:
        if not isinstance(items, (list, tuple)):
            return
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in items]
        self.settings_window.set_audio_devices(devices, self.runtime.settings.audio.device_id)
        if self.onboarding is not None:
            self.onboarding.set_microphones(devices, self.runtime.settings.audio.device_id)

    def _on_models_ready(self, installed: object) -> None:
        self._set_model_choices(
            set(installed) if isinstance(installed, (set, frozenset)) else set()
        )

    def _save_settings(self, values: object) -> None:
        if not isinstance(values, dict):
            return
        previous = self.runtime.settings
        previous_autostart = previous.general.start_with_windows
        updated = self.runtime.settings_repository.update(values)
        vocabulary = Vocabulary(updated.dictation.custom_vocabulary)
        if updated.dictation.custom_vocabulary != list(vocabulary.entries):
            updated.dictation.custom_vocabulary = list(vocabulary.entries)
            self.runtime.settings_repository.save(updated)
        if (
            updated.dictation.model_profile == previous.dictation.model_profile
            and updated.model.installed_model != previous.model.installed_model
        ):
            updated.dictation.model_profile = domain_profile(
                _coerce_asr_profile(updated.model.installed_model)
            )
            self.runtime.settings_repository.save(updated)
        self.runtime.settings = updated
        self.controller.settings = updated
        self.controller.vocabulary = vocabulary
        self.controller.target_manager.set_policy(TargetPolicy(updated.dictation.target_mode.value))
        self.controller.injection_service.strategy = updated.dictation.injection_mode.value
        self.controller.injection_service.paste_delay = (
            max(350, updated.dictation.insertion_delay_ms) / 1_000.0
        )
        self.main.set_minimize_to_tray(updated.general.minimize_to_tray)
        self.main.set_interface_language(updated.general.ui_language)
        self.main.set_toggle_hotkey(updated.hotkeys.toggle)
        apply_theme(self.application, ThemeMode(updated.general.theme))
        if updated.general.start_with_windows != previous_autostart:
            self.runtime.startup.set_enabled(updated.general.start_with_windows)
        self._configure_capture_factory()
        self._configure_segmenter_factory()
        if not self._configure_hotkeys():
            self.runtime.settings_repository.save(previous)
            self.runtime.settings = previous
            self.controller.settings = previous
            self.controller.vocabulary = Vocabulary(previous.dictation.custom_vocabulary)
            self.controller.target_manager.set_policy(
                TargetPolicy(previous.dictation.target_mode.value)
            )
            self.controller.injection_service.strategy = previous.dictation.injection_mode.value
            self.controller.injection_service.paste_delay = (
                max(350, previous.dictation.insertion_delay_ms) / 1_000.0
            )
            self.runtime.startup.set_enabled(previous.general.start_with_windows)
            self.main.set_minimize_to_tray(previous.general.minimize_to_tray)
            self.main.set_interface_language(previous.general.ui_language)
            self.main.set_toggle_hotkey(previous.hotkeys.toggle)
            apply_theme(self.application, ThemeMode(previous.general.theme))
            self._configure_capture_factory()
            self._configure_segmenter_factory()
            self._configure_hotkeys()
            self.settings_window.set_diagnostics_status(
                "Настройки не сохранены: одна из горячих клавиш занята", False
            )
            self.settings_window.set_values(previous)
            self.settings_window.show()
            self.settings_window.raise_()
            return
        backend_settings_changed = (
            updated.model.installed_model != previous.model.installed_model
            or updated.model.cpu_threads != previous.model.cpu_threads
            or updated.model.power_mode != previous.model.power_mode
            or updated.dictation.model_profile != previous.dictation.model_profile
        )
        selected = _coerce_asr_profile(updated.dictation.model_profile.value)
        required_model = DEFAULT_MODEL_SPECS[selected].model_id
        if backend_settings_changed and self.runtime.model_manager.is_installed(required_model):
            self.controller.configure_backend(
                self.runtime.backend_for(selected),
                domain_profile(selected),
                self.runtime.retry_backend_for(selected),
            )
        elif backend_settings_changed:
            self.settings_window.set_diagnostics_status(
                f"Для выбранного профиля сначала скачайте модель {required_model}", False
            )

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
                AudioCaptureConfig(
                    # None asks PortAudio for the current default at session
                    # start, so a Windows default-device change needs no app
                    # restart. Explicit user selections remain pinned.
                    device=(
                        device.index if self.runtime.settings.audio.device_id is not None else None
                    ),
                    input_sample_rate=rate,
                    callback_queue_chunks=2048,
                ),
                preprocessing=processing,
            )

        self.controller.capture_factory = factory
        self.controller.microphone_name = f"{device.name} ({device.host_api})"
        self.runtime.device = device

    def _configure_segmenter_factory(self) -> None:
        vad_path = self.runtime.data_directory / "models" / "silero-vad" / "silero_vad.onnx"
        sensitivity = self.runtime.settings.audio.vad_sensitivity

        def factory() -> StreamingSegmenter:
            threshold = min(0.75, max(0.25, 0.70 - sensitivity * 0.5))
            settings = replace(SegmentationSettings(), speech_threshold=threshold)
            return StreamingSegmenter(SileroVAD(vad_path, strict=False), settings=settings)

        self.controller.segmenter_factory = factory

    def show_history(self) -> None:
        self._refresh_history()
        self.history_window.show()
        self.history_window.raise_()

    def _refresh_history(self) -> None:
        self.history_window.set_sessions(list(self.runtime.session_repository.recent(250)))

    def _delete_history_session(self, session_id: str) -> None:
        if self.runtime.session_repository.delete(session_id):
            _remove_owned_path(
                self.runtime.data_directory / "audio" / f"{session_id}.wav",
                self.runtime.data_directory,
            )
            _remove_owned_path(self.controller.spool_root / session_id, self.controller.spool_root)
            self.history_window.remove_session(session_id)

    def _recover_history_session(self, session_id: str) -> None:
        def worker() -> None:
            try:
                text = self.controller.recover_session(session_id)
                self.signals.recovery_ready.emit(session_id, True, text)
            except Exception as exc:
                _LOGGER.exception("session_recovery_failed type=%s", type(exc).__name__)
                self.signals.recovery_ready.emit(
                    session_id, False, f"Восстановление не завершено: {type(exc).__name__}"
                )

        threading.Thread(target=worker, name="voxtype-session-recovery", daemon=True).start()

    def _on_recovery_ready(self, _session_id: str, ok: bool, message: str) -> None:
        if ok:
            QApplication.clipboard().setText(message)
            self.tray.show_message("VoxType", "Текст и сохранённый аудиохвост восстановлены")
        else:
            self.tray.show_message("VoxType", message, critical=True)
        self._refresh_history()

    def _on_pending_flush_ready(self) -> None:
        self._pending_flush_running = False

    def _export_history_session(self, session_id: str) -> None:
        record = self.runtime.session_repository.get(session_id)
        if record is None:
            return
        selected, _filter = QFileDialog.getSaveFileName(
            self.history_window,
            "Экспорт текста",
            f"VoxType-{session_id}.txt",
            "Text files (*.txt)",
        )
        if selected:
            Path(selected).write_text(record.committed_text, encoding="utf-8")

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
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in self.runtime.devices]
        wizard.set_microphones(devices, self.runtime.device.index)
        wizard.microphone_refresh_requested.connect(self._refresh_onboarding_microphones)
        wizard.microphone_selected.connect(self._audio_test)
        wizard.calibration_requested.connect(self._calibrate)
        wizard.hardware_probe_requested.connect(self._recommend_hardware)
        wizard.model_download_requested.connect(self._download_model)
        wizard.phrase_test_requested.connect(self._test_phrase)
        wizard.insertion_test_requested.connect(self._test_insertion)
        wizard.setup_completed.connect(self._complete_onboarding)
        wizard.show()
        self._run_background("voxtype-onboarding-devices", self._scan_settings_devices)

    def _refresh_onboarding_microphones(self) -> None:
        if self.onboarding is None:
            return
        self._run_background("voxtype-onboarding-devices", self._scan_settings_devices)

    def _recommend_hardware(self) -> None:
        def worker() -> None:
            hardware = probe_hardware()
            # RAM alone cannot prove realtime inference.  Begin with the measured
            # conservative profile; the phrase benchmark may promote a faster CPU.
            summary = (
                f"{hardware.cpu_name}; {hardware.total_ram_bytes / 1024**3:.1f} ГБ RAM. "
                "Окончательный выбор можно изменить после локального benchmark."
            )
            self.signals.hardware_ready.emit("fast", summary)

        threading.Thread(target=worker, name="voxtype-hardware-probe", daemon=True).start()

    def _on_hardware_ready(self, profile: str, summary: str) -> None:
        if self.onboarding is not None:
            self.onboarding.set_hardware_recommendation(profile, summary)

    def _calibrate(self, device_text: str) -> None:
        try:
            device = int(device_text)
        except ValueError:
            device = self.runtime.device.index

        def worker() -> None:
            capture_rate = 48_000
            try:
                import sounddevice as sd

                selected = next(
                    (candidate for candidate in self.runtime.devices if candidate.index == device),
                    self.runtime.device,
                )
                capture_rate = choose_capture_rate(selected)
                silence_blocks: list[np.ndarray[Any, np.dtype[np.float32]]] = []
                speech_blocks: list[np.ndarray[Any, np.dtype[np.float32]]] = []
                phase_seconds = ((2.5, silence_blocks), (5.0, speech_blocks))
                block_frames = max(1, round(capture_rate * 0.1))
                with sd.InputStream(
                    device=device,
                    samplerate=capture_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=block_frames,
                ) as stream:
                    elapsed_total = 0.0
                    for phase_index, (duration, destination) in enumerate(phase_seconds):
                        phase_frames = round(capture_rate * duration)
                        captured = 0
                        while captured < phase_frames:
                            frames = min(block_frames, phase_frames - captured)
                            block, _overflowed = stream.read(frames)
                            destination.append(np.asarray(block, dtype=np.float32).copy())
                            captured += frames
                            elapsed_total += frames / capture_rate
                            percent = min(89, round(elapsed_total / 7.5 * 90))
                            instruction = (
                                "Пожалуйста, помолчите 2–3 секунды."
                                if phase_index == 0
                                else "Теперь произнесите обычным голосом несколько предложений."
                            )
                            self.signals.calibration_progress.emit(percent, instruction)
                self.signals.calibration_progress.emit(90, "Анализирую измеренный сигнал…")
                result = analyze_calibration(
                    np.concatenate(silence_blocks),
                    np.concatenate(speech_blocks),
                    sample_rate=capture_rate,
                )
            except Exception as exc:
                result = device_error_result(type(exc).__name__, sample_rate=capture_rate)
            self.signals.calibration_ready.emit(result)

        threading.Thread(target=worker, name="voxtype-calibration", daemon=True).start()

    def _on_calibration_progress(self, percent: int, message: str) -> None:
        if self.onboarding is not None:
            self.onboarding.set_calibration_progress(percent, message)

    def _on_calibration_ready(self, result: CalibrationResult) -> None:
        if self.onboarding is not None:
            self.onboarding.set_calibration_result(result)

    def _on_audio_level(self, level: float, clipping: float, message: str) -> None:
        if self.onboarding is not None and self.onboarding.isVisible():
            self.onboarding.update_microphone_level(level, clipping, message)

    def _download_model(self, profile_text: str) -> None:
        selected = _coerce_asr_profile(profile_text)
        spec = DEFAULT_MODEL_SPECS[selected]

        def worker() -> None:
            try:

                def progress(done: int, total: int | None) -> None:
                    percent = int(done * 100 / total) if total else 0
                    self.signals.model_progress.emit(percent, f"{done / 1024**2:.0f} МБ")

                self.runtime.model_manager.install(spec, progress=progress)
                install_silero_vad(self.runtime.data_directory)
                backend = self.runtime.backend_for(selected)
                configured = self.controller.configure_backend(
                    backend,
                    domain_profile(selected),
                    self.runtime.retry_backend_for(selected),
                )
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

    def _benchmark_model(self, model_id: str) -> None:
        self.settings_window.set_diagnostics_status("Говорите обычным голосом 6 секунд…")

        def worker() -> None:
            try:
                import sounddevice as sd

                seconds = 6
                sample_rate = 16_000
                capture_rate = choose_capture_rate(self.runtime.device)
                recorded = sd.rec(
                    seconds * capture_rate,
                    samplerate=capture_rate,
                    channels=1,
                    dtype="float32",
                    device=self.runtime.device.index,
                )
                sd.wait()
                audio: np.ndarray[Any, np.dtype[np.float32]] = np.asarray(
                    recorded, dtype=np.float32
                ).reshape(-1)
                if capture_rate != sample_rate:
                    audio = resample_audio(audio, capture_rate, sample_rate)
                selected = _coerce_asr_profile(model_id)
                spec = DEFAULT_MODEL_SPECS[selected]
                if not self.runtime.model_manager.is_installed(spec.model_id):
                    raise RuntimeError("selected model is not installed")
                backend = self.runtime.backend_for(selected)
                result = benchmark_backend(
                    selected,
                    backend,
                    (BenchmarkCase(TranscriptionRequest(audio, sample_rate, "ru"), "microphone"),),
                )
                save_benchmark_results(
                    self.runtime.data_directory / "benchmark-results.json", (result,)
                )
                rtf = result.maximum_rtf
                ok = result.successful and rtf < 1.0
                if ok:
                    self.controller.configure_backend(
                        backend,
                        domain_profile(selected),
                        self.runtime.retry_backend_for(selected),
                    )
                quality = "есть запас" if rtf <= 0.7 else "рекомендуется профиль «Быстро»"
                message = f"RTF={rtf:.3f}; {quality}"
            except Exception as exc:
                ok, message = False, f"Benchmark не завершён: {type(exc).__name__}"
            self.signals.settings_status.emit(message, ok)

        threading.Thread(target=worker, name="voxtype-model-benchmark", daemon=True).start()

    def _audio_test(self, device_text: str) -> None:
        try:
            device = int(device_text)
        except ValueError:
            device = self.runtime.device.index

        def worker() -> None:
            try:
                import sounddevice as sd

                audio = sd.rec(
                    48_000 * 2,
                    samplerate=48_000,
                    channels=1,
                    dtype="float32",
                    device=device,
                )
                sd.wait()
                rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
                clipping = float(np.mean(np.abs(audio) >= 0.98) * 100.0)
                self.signals.audio_level.emit(rms, clipping, "Говорите обычным голосом")
            except Exception as exc:
                self.signals.audio_level.emit(
                    0.0, 0.0, f"Тест микрофона не завершён: {type(exc).__name__}"
                )

        threading.Thread(target=worker, name="voxtype-audio-test", daemon=True).start()

    def _delete_local_data(self) -> None:
        if self.controller.state.name != "IDLE":
            self.settings_window.set_diagnostics_status(
                "Сначала завершите активную диктовку", False
            )
            return
        self.controller.backend.unload()
        if (
            self.controller.retry_backend is not None
            and self.controller.retry_backend is not self.controller.backend
        ):
            self.controller.retry_backend.unload()
        removed = self.runtime.session_repository.delete_all()
        root_logger = logging.getLogger("voxtype")
        for handler in tuple(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        log_root = self.runtime.log_path.parent.resolve()
        data_root = self.runtime.data_directory.resolve()
        for child in tuple(data_root.iterdir()):
            if child.name.startswith("sessions.sqlite3"):
                continue
            _remove_owned_path(child, data_root)
        spool_root = self.controller.spool_root.resolve()
        if spool_root.exists():
            for child in tuple(spool_root.iterdir()):
                _remove_owned_path(child, spool_root)
        config_root = self.runtime.settings_repository.path.parent.resolve()
        if config_root.exists():
            for child in tuple(config_root.iterdir()):
                _remove_owned_path(child, config_root)
        if log_root.exists():
            for child in tuple(log_root.iterdir()):
                _remove_owned_path(child, log_root)
        self.runtime.log_path = configure_logging()
        defaults = create_default_settings()
        self.runtime.settings = defaults
        self.controller.settings = defaults
        self.runtime.startup.disable()
        self.settings_window.set_diagnostics_status(
            f"Локальные данные удалены; сессий: {removed}. Модели нужно скачать заново.", True
        )

    def _open_log(self) -> None:
        os.startfile(self.runtime.log_path)

    def _export_diagnostics(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(
            self.settings_window,
            "Экспорт диагностики",
            "VoxType-diagnostics.json",
            "JSON (*.json)",
        )
        if not selected:
            return
        hardware = probe_hardware()
        payload = {
            "version": __version__,
            "hardware": {
                "os": hardware.os_name,
                "cpu": hardware.cpu_name,
                "physical_cores": hardware.physical_cores,
                "logical_cores": hardware.logical_cores,
                "ram_bytes": hardware.total_ram_bytes,
                "gpu": hardware.gpu_names,
            },
            "metrics": self.controller.metrics.snapshot(),
            "checks": [
                {"name": item.name, "ok": item.ok, "detail": item.detail}
                for item in run_health_checks(self.runtime.data_directory)
            ],
        }
        Path(selected).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
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
                    mono = resample_audio(mono, rate, 16_000)
                language_code = cast(Literal["ru", "en"], language)
                result = self.controller.backend.transcribe(
                    TranscriptionRequest(mono, 16_000, language_code)
                )
                text = result.text
                ok = bool(text.strip())
                rtf = result.processing_seconds / max(result.audio_duration_seconds, 1e-9)
                selected = _coerce_asr_profile(self.controller.backend.model_id)
                if rtf > 0.7 and selected is not ASRProfile.FAST:
                    fast_spec = DEFAULT_MODEL_SPECS[ASRProfile.FAST]

                    def progress(done: int, total: int | None) -> None:
                        percent = int(done * 100 / total) if total else 0
                        self.signals.model_progress.emit(
                            percent, "Устанавливаю быстрый профиль по результату RTF"
                        )

                    self.runtime.model_manager.install(fast_spec, progress=progress)
                    fast_backend = self.runtime.backend_for(ASRProfile.FAST)
                    fast_result = benchmark_backend(
                        ASRProfile.FAST,
                        fast_backend,
                        (
                            BenchmarkCase(
                                TranscriptionRequest(mono, 16_000, language_code),
                                f"onboarding-{language_code}",
                            ),
                        ),
                    )
                    if fast_result.successful and fast_result.maximum_rtf < rtf:
                        self.controller.configure_backend(
                            fast_backend,
                            domain_profile(ASRProfile.FAST),
                            self.runtime.retry_backend_for(ASRProfile.FAST),
                        )
                        self.runtime.settings = self.runtime.settings_repository.load()
                        rtf = fast_result.maximum_rtf
                self.signals.settings_status.emit(
                    f"Фраза {language.upper()}: RTF={rtf:.3f}", rtf < 1.0
                )
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

    def close(self) -> bool:
        if self._closed:
            return True
        if self._closing:
            return False
        self._closing = True
        self.timer.stop()
        self._heartbeat_timer.stop()
        self._heartbeat_stop.set()
        self.tray.show_message("VoxType", "Безопасно завершаю работу в фоне")

        def worker() -> None:
            self.signals.shutdown_ready.emit(self.runtime.close())

        threading.Thread(target=worker, name="voxtype-shutdown", daemon=True).start()
        return False

    def _on_shutdown_ready(self, closed: bool) -> None:
        if not closed:
            self._closing = False
            self.timer.start()
            self._heartbeat_stop.clear()
            self._heartbeat_timer.start()
            threading.Thread(
                target=self._heartbeat_monitor, name="voxtype-ui-watchdog", daemon=True
            ).start()
            self.tray.show_message(
                "VoxType", "Завершение inference продолжается; повторите выход позже"
            )
            return
        self._closed = True
        self.hotkeys.close()
        self.overlay.close()
        self.tray.close()
        self.main.force_close()
        self.application.quit()


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _remove_owned_path(path: Path, root: Path) -> None:
    resolved = path.resolve()
    owned_root = root.resolve()
    if resolved == owned_root or owned_root not in resolved.parents or not resolved.exists():
        return
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()


def _coerce_asr_profile(value: str) -> ASRProfile:
    normalized = value.casefold().strip()
    if normalized in {
        "accurate",
        "large-v3",
        "large-v3-turbo",
        "turbo",
        "maximum_accuracy",
    }:
        return ASRProfile.MAXIMUM_ACCURACY
    if normalized == "balanced":
        return ASRProfile.BALANCED
    return ASRProfile.FAST


def run_desktop(runtime: RuntimeBundle | None = None) -> int:
    existing = QApplication.instance()
    application = cast(QApplication, existing) if existing is not None else QApplication([])
    application.setApplicationName("VoxType")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("VoxType")
    shell = DesktopApplication(application, runtime or build_runtime())
    previous_excepthook = sys.excepthook

    def ui_excepthook(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        _LOGGER.critical(
            "unhandled_ui_exception kind=%s",
            exception_type.__name__,
            exc_info=(exception_type, exception, traceback),
        )
        shell.controller.handle_ui_exception(exception)

    sys.excepthook = ui_excepthook
    # Keep a Python reference for the full Qt event loop lifetime.
    application.setProperty("voxtype_shell", shell)
    try:
        return int(application.exec())
    finally:
        sys.excepthook = previous_excepthook


__all__ = ["DesktopApplication", "run_desktop"]
