"""PySide6 desktop application shell for VoxType."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

import numpy as np
from PySide6.QtCore import (
    QAbstractEventDispatcher,
    QAbstractNativeEventFilter,
    QByteArray,
    QObject,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QWidget

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
from voxtype.audio.devices import (
    AudioBackendUnavailable,
    choose_capture_channels,
    choose_capture_rate,
    list_input_devices,
    resolve_recording_device,
)
from voxtype.audio.preprocessing import (
    PreprocessingProfile as AudioPreprocessingProfile,
)
from voxtype.audio.preprocessing import PreprocessorSettings
from voxtype.audio.recording import record_input
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
from voxtype.ui.messages import failure_message
from voxtype.ui.professional import ProfessionalWindow
from voxtype.ui.desktop_shell import CommandPalette
from voxtype.ui.obsidian import ModelDownloadDialog
from voxtype.core.profiles import DictationProfile
from voxtype.transcript.professional import VoiceAction
from voxtype.vad.segmenter import SegmentationSettings, StreamingSegmenter
from voxtype.vad.silero import SileroVAD
from voxtype.version import __version__
from voxtype.windows.hotkeys import GlobalHotkeyManager, parse_hotkey
from voxtype.windows.power_events import PowerEventCoordinator
from voxtype.windows.send_input import SendInputController
from voxtype.windows.single_instance import WM_VOXTYPE_RESTORE
from voxtype.windows.target_window import TargetPolicy

_LOGGER = logging.getLogger("voxtype.app")


class _WorkerSignals(QObject):
    professional_action = Signal(int, object, object)
    professional_delivery_failed = Signal()
    dashboard_copied = Signal(bool)
    workflow_action = Signal(str, str, str)
    snapshot_ready = Signal(object)
    model_progress = Signal(int, str)
    model_ready = Signal(bool, str, str)
    calibration_progress = Signal(int, str)
    calibration_ready = Signal(object)
    phrase_ready = Signal(str, str, bool)
    diagnostics_ready = Signal(object)
    settings_status = Signal(str, object)
    audio_level = Signal(float, float, str)
    recovery_ready = Signal(str, bool, str)
    history_ready = Signal(int, object)
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
        restore_request: Any,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.power_events = power_events
        self.shutdown_query = shutdown_query
        self.restore_request = restore_request

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
            if int(native.message) == WM_VOXTYPE_RESTORE:
                self.restore_request()
                return True, 0
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
        startup_started = time.perf_counter()
        previous_stage = startup_started

        def stage(name: str) -> None:
            nonlocal previous_stage
            now = time.perf_counter()
            _LOGGER.info(
                "startup_stage name=%s duration_ms=%.1f total_ms=%.1f",
                name,
                (now - previous_stage) * 1_000.0,
                (now - startup_started) * 1_000.0,
            )
            previous_stage = now

        self.application = application
        self._gui_thread_ident = threading.get_ident()
        self.runtime = runtime
        self.controller = runtime.controller
        self.signals = _WorkerSignals()
        self.controller.professional_action = self.signals.professional_action.emit
        self.controller.workflows.desktop_action = self.signals.workflow_action.emit
        self.main = MainWindow(
            None,
            interface_language=runtime.settings.general.ui_language,
            minimize_to_tray=runtime.settings.general.minimize_to_tray,
        )
        if os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip():
            geometry = QSettings(str(runtime.data_directory / "ui.ini"), QSettings.Format.IniFormat)
        else:
            geometry = QSettings("VoxType", "VoxType")
        self.main.set_geometry_store(geometry)
        self._view_preferences = geometry
        self.application.setProperty("voxtypeReduceMotion", geometry.value("ui/reduceMotion", False, type=bool))
        self._model_download_dialog: ModelDownloadDialog | None = None
        self._open_palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self.main)
        self._open_palette_shortcut.activated.connect(self.show_command_palette)
        stage("main_window_constructed")
        self._overlay: DictationOverlay | None = None
        self.tray = TrayController(None)
        self._settings_window: SettingsWindow | None = None
        self._professional_window: ProfessionalWindow | None = None
        self._file_queue = None
        self._interactive_asr = threading.Event()
        self._professional_timer = QTimer(self.main, interval=750)
        self._professional_timer.timeout.connect(self._poll_professional)
        QTimer.singleShot(1100, self._start_professional_queue)
        self._history_window: HistoryWindow | None = None
        self._diagnostics_window: DiagnosticsWindow | None = None
        stage("auxiliary_widgets_constructed")
        self.onboarding: OnboardingWizard | None = None
        self.hotkeys = GlobalHotkeyManager(hwnd=0)
        self._resume_after_suspend = False
        self.power_events = PowerEventCoordinator(
            on_suspend=self._on_suspend, on_resume=self._on_resume
        )
        self.hotkey_filter = _HotkeyEventFilter(
            self.hotkeys,
            self.power_events,
            self.close,
            lambda: QTimer.singleShot(0, self._show_main),
        )
        self.timer = QTimer(self.main)
        self.timer.setInterval(50)
        self._last_state = "STARTUP"
        self._last_rendered_snapshot: object | None = None
        self._result_dismissed = False
        self._last_pending_flush = 0.0
        self._last_resource_sample = 0.0
        self._pending_flush_running = False
        self._history_revision = 0
        self._history_loading = False
        self._closed = False
        self._closing = False
        self._heartbeat_last = time.monotonic()
        self._dispatch_started: float | None = None
        self._event_dispatcher = QAbstractEventDispatcher.instance(application.thread())
        if self._event_dispatcher is not None:
            self._event_dispatcher.awake.connect(self._dispatch_awake)
            self._event_dispatcher.aboutToBlock.connect(self._dispatch_done)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_timer = QTimer(self.main)
        self._heartbeat_timer.setInterval(100)
        self._heartbeat_timer.timeout.connect(self._heartbeat)
        if runtime.settings.general.start_with_windows:
            runtime.startup.enable()
        self._connect()
        self._configure_hotkeys(strict=False)
        stage("signals_and_hotkeys_connected")
        self.application.installNativeEventFilter(self.hotkey_filter)
        apply_theme(application, ThemeMode(runtime.settings.general.theme))
        stage("stylesheet_applied")
        self.application.setWindowIcon(create_app_icon())
        self.application.setQuitOnLastWindowClosed(False)
        # A native show/tray call can stall before the Qt event loop starts.
        # Start the watchdog before exposing the HWND so it can capture that stack.
        self._heartbeat_last = time.monotonic()
        threading.Thread(
            target=self._heartbeat_monitor, name="voxtype-ui-watchdog", daemon=True
        ).start()
        self.main.show()
        stage("main_window_shown")
        self.main.set_toggle_hotkey(runtime.settings.hotkeys.toggle)
        self.controller.target_manager.register_own_window(int(self.main.winId()))
        self.tray.show()
        stage("tray_shown")
        if runtime.settings_repository.last_recovery_backup is not None:
            self.tray.show_message(
                "Настройки восстановлены",
                "Повреждённые настройки сохранены для диагностики. "
                "VoxType применил безопасные значения по умолчанию.",
            )
        if runtime.session_repository.last_recovery_backup is not None:
            self.tray.show_message(
                "История восстановлена",
                "Повреждённая база истории сохранена для диагностики. "
                "VoxType создал новую рабочую историю.",
            )
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._heartbeat_last = time.monotonic()
        self._heartbeat_timer.start()
        stage("qt_timers_started")
        if not runtime.settings.general.onboarding_completed:
            QTimer.singleShot(100, self.show_onboarding)
        if not self.controller.policy.disable_history and runtime.session_repository.unfinished():
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
        self.main.shell.page_requested.connect(self._navigate_desktop)
        self.main.shell.about_requested.connect(self.show_diagnostics)
        self.main.shell.palette_requested.connect(self.show_command_palette)
        self.main.copy_result_requested.connect(
            self._copy_dashboard_result
        )
        self.signals.dashboard_copied.connect(
            lambda copied: self._notify_desktop(
                "copy-transcript", "Текст скопирован" if copied else "Не удалось скопировать текст",
                "success" if copied else "warning",
            )
        )
        self.main.insert_result_requested.connect(self._insert_dashboard_result)
        self.main.cancel_processing_requested.connect(
            lambda: self._invoke_ui("main.cancel", self.controller.cancel_processing)
        )
        self.main.professional_requested.connect(self.show_professional_tools)
        self.main.exit_requested.connect(self.quit)
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
        self.signals.model_progress.connect(self._on_model_progress)
        self.signals.model_ready.connect(self._on_model_ready)
        self.signals.calibration_progress.connect(self._on_calibration_progress)
        self.signals.calibration_ready.connect(self._on_calibration_ready)
        self.signals.phrase_ready.connect(self._on_phrase_ready)
        self.signals.diagnostics_ready.connect(self._on_diagnostics_ready)
        self.signals.audio_level.connect(self._on_audio_level)
        self.signals.recovery_ready.connect(self._on_recovery_ready)
        self.signals.professional_action.connect(
            self._on_professional_action, Qt.ConnectionType.QueuedConnection
        )
        self.signals.professional_delivery_failed.connect(
            lambda: self.tray.show_message("VoxType", "Не удалось отправить текст в целевое окно", critical=True)
        )
        self.signals.workflow_action.connect(
            self._on_workflow_action, Qt.ConnectionType.QueuedConnection
        )
        self.signals.snapshot_ready.connect(self._update_views_if_changed)
        self.signals.history_ready.connect(
            self._on_history_ready, Qt.ConnectionType.QueuedConnection
        )
        self.signals.pending_flush_ready.connect(self._on_pending_flush_ready)
        self.signals.hardware_ready.connect(self._on_hardware_ready)
        self.signals.devices_ready.connect(self._on_devices_ready)
        self.signals.models_ready.connect(self._on_models_ready)
        self.signals.shutdown_ready.connect(self._on_shutdown_ready)
        self.application.aboutToQuit.connect(self.close)

    @property
    def overlay(self) -> DictationOverlay:
        overlay = self._overlay
        if overlay is None:
            settings = None
            if os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip():
                settings = QSettings(
                    str(self.runtime.data_directory / "overlay.ini"), QSettings.Format.IniFormat
                )
            overlay = DictationOverlay(None, settings=settings)
            self._overlay = overlay
            overlay.pause_requested.connect(
                lambda: self._invoke_ui("overlay.toggle_pause", self.controller.toggle_pause)
            )
            overlay.stop_requested.connect(
                lambda: self._invoke_ui("overlay.stop", self.controller.toggle_dictation)
            )
            overlay.cancel_processing_requested.connect(
                lambda: self._invoke_ui("overlay.cancel", self.controller.cancel_processing)
            )
            overlay.copy_all_requested.connect(
                lambda: self._run_background(
                    "voxtype-copy-result", self.controller.copy_full_transcript
                )
            )
            overlay.insert_requested.connect(
                lambda: self._run_background(
                    "voxtype-retry-insertion", self.controller.retry_completed_insertion
                )
            )
            overlay.close_result_requested.connect(self._dismiss_result)
        return overlay

    @property
    def settings_window(self) -> SettingsWindow:
        window = self._settings_window
        if window is None:
            window = SettingsWindow(self.main)
            self._settings_window = window
            window.set_view_preferences(self._view_preferences)
            window.set_privacy_policy(
                history_disabled=self.controller.policy.disable_history,
                audio_disabled=self.controller.policy.disable_audio_storage,
            )
            window.settings_submitted.connect(self._save_settings)
            window.onboarding_requested.connect(self.show_onboarding)
            window.open_data_folder_requested.connect(self._open_data_folder)
            window.model_download_requested.connect(self._download_model)
            window.model_delete_requested.connect(self._delete_model)
            window.model_benchmark_requested.connect(self._benchmark_model)
            window.audio_test_requested.connect(self._audio_test)
            window.delete_local_data_requested.connect(self._delete_local_data)
            window.open_log_requested.connect(self._open_log)
            window.export_diagnostics_requested.connect(self._export_diagnostics)
            window.show_versions_requested.connect(self.show_diagnostics)
            window.self_test_requested.connect(self.show_diagnostics)
            self.signals.settings_status.connect(window.set_diagnostics_status)
            self.signals.audio_level.connect(window.update_microphone_level)
        return window

    @property
    def history_window(self) -> HistoryWindow:
        window = self._history_window
        if window is None:
            window = HistoryWindow(self.main)
            self._history_window = window
            window.refresh_requested.connect(self._refresh_history)
            window.enable_repository_search()
            window.insert_requested.connect(self._submit_professional_text)
            window.clear_requested.connect(self._clear_finished_history)
            window.delete_requested.connect(self._delete_history_session)
            window.recover_requested.connect(self._recover_history_session)
            window.export_requested.connect(self._export_history_session)
        return window

    @property
    def diagnostics_window(self) -> DiagnosticsWindow:
        window = self._diagnostics_window
        if window is None:
            window = DiagnosticsWindow(self.main)
            self._diagnostics_window = window
            window.refresh_requested.connect(self._refresh_diagnostics)
            window.self_test_requested.connect(self._refresh_diagnostics)
            window.open_log_requested.connect(self._open_log)
            window.export_requested.connect(self._export_diagnostics)
        return window

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

    def _dispatch_awake(self) -> None:
        self._dispatch_started = time.perf_counter()

    def _dispatch_done(self) -> None:
        started = self._dispatch_started
        self._dispatch_started = None
        if started is None:
            return
        duration_ms = (time.perf_counter() - started) * 1_000.0
        if duration_ms >= 100.0:
            _LOGGER.warning(
                "UI_DISPATCH_SLOW state=%s duration_ms=%.1f",
                self.controller.state.name,
                duration_ms,
            )

    def _heartbeat_monitor(self) -> None:
        reported = False
        stack_reported = False
        while not self._heartbeat_stop.wait(0.1):
            elapsed = time.monotonic() - self._heartbeat_last
            if elapsed > 0.5 and not reported:
                _LOGGER.warning(
                    "UI_STALL state=%s elapsed_ms=%.1f",
                    self._last_state,
                    elapsed * 1_000.0,
                )
                reported = True
            if elapsed > 2.0 and not stack_reported:
                frame = sys._current_frames().get(self._gui_thread_ident)
                if frame is not None:
                    _LOGGER.error(
                        "UI_STALL_STACK state=%s elapsed_ms=%.1f\n%s",
                        self._last_state,
                        elapsed * 1_000.0,
                        "".join(traceback.format_stack(frame, limit=40)),
                    )
                stack_reported = True
            elif elapsed <= 0.25:
                reported = False
                stack_reported = False

    def _show_main(self) -> None:
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def _dismiss_result(self) -> None:
        self._result_dismissed = True
        if self._overlay is not None:
            self._overlay.hide()

    def _update_views_if_changed(self, snapshot: object) -> None:
        # Idle and completed snapshots are often identical for many polls.
        # Rewriting every Qt property also repaints the overlay and tray.
        if snapshot == self._last_rendered_snapshot:
            return
        self.main.update_snapshot(snapshot)
        profiles = getattr(self.controller, "profiles", None)
        if profiles is not None:
            chosen = next((profile.name for profile in profiles.profiles() if profile.id == profiles.default_id), "")
            self.main.set_profile_name(chosen)
        self.tray.update_snapshot(snapshot)
        if self._overlay is not None:
            self._overlay.update_snapshot(snapshot)
        self._last_rendered_snapshot = snapshot

    def _poll(self) -> None:
        poll_started = time.perf_counter()
        if self.controller.state.name in {"IDLE", "COMPLETED", "ERROR_RECOVERABLE"} and not self.controller.injection_service.pending_fragments:
            with suppress(OSError, RuntimeError):
                self.controller.target_manager.remember_foreground_target()
        target_done = time.perf_counter()
        snapshot = self.controller.snapshot()
        snapshot_done = time.perf_counter()
        self.signals.snapshot_ready.emit(snapshot)
        widgets_done = time.perf_counter()
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
            overlay = self._overlay
            if overlay is None:
                overlay = self.overlay
                overlay.update_snapshot(snapshot)
            overlay.show_without_activating()
        else:
            if self._overlay is not None:
                self._overlay.hide()
        if snapshot.state != self._last_state and snapshot.state == "ERROR_RECOVERABLE":
            message = snapshot.error_message or snapshot.status_message
            if self.main.isVisible():
                self.main.shell.notifications.notify(
                    "dictation-error", "Диктовка требует внимания", "error", details=message,
                )
            else:
                self.tray.show_message("VoxType", message, critical=True)
        if (
            snapshot.state != self._last_state
            and self.runtime.settings.general.play_sounds
            and snapshot.state in {"LISTENING", "IDLE", "ERROR_RECOVERABLE"}
        ):
            QApplication.beep()
        self._last_state = snapshot.state
        now = time.monotonic()
        if (
            snapshot.state in {"PAUSED_TARGET_LOST", "COMPLETED"}
            and snapshot.delivery_status not in {"copy_required", "no_result"}
            and self.controller.injection_service.pending_fragments
            and now - self._last_pending_flush >= 0.5
            and not self._pending_flush_running
        ):
            self._last_pending_flush = now
            self._pending_flush_running = True

            def flush_worker() -> None:
                try:
                    if snapshot.state == "COMPLETED":
                        self.controller.retry_completed_insertion()
                    else:
                        self.controller.flush_pending_text()
                finally:
                    self.signals.pending_flush_ready.emit()

            threading.Thread(target=flush_worker, name="voxtype-pending-flush", daemon=True).start()
        if now - self._last_resource_sample >= 10.0:
            self._last_resource_sample = now
            self.controller.sample_resources()
        poll_ms = (time.perf_counter() - poll_started) * 1_000.0
        if poll_ms >= 50.0:
            _LOGGER.warning(
                "UI_POLL_SLOW state=%s duration_ms=%.1f target_ms=%.1f snapshot_ms=%.1f widgets_ms=%.1f other_ms=%.1f",
                snapshot.state,
                poll_ms,
                (target_done - poll_started) * 1_000.0,
                (snapshot_done - target_done) * 1_000.0,
                (widgets_done - snapshot_done) * 1_000.0,
                (time.perf_counter() - widgets_done) * 1_000.0,
            )

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

    def _configure_hotkeys(self, *, strict: bool = True) -> bool:
        if self.hotkeys.unregister_all():
            self.tray.show_message(
                "Горячие клавиши", "Не удалось освободить старые сочетания", critical=True
            )
            return False
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
        palette = parse_hotkey("Ctrl+K", identifier=4, name="command_palette")
        self._open_palette_shortcut.setEnabled(not any(
            self._shortcut_conflicts(spec, palette) for spec, _identifier, _name, _callback in definitions
        ))
        failures: list[str] = []
        for specification, identifier, name, callback in definitions:
            try:
                hotkey = parse_hotkey(specification, identifier=identifier, name=name)

                def invoke(
                    _item: object,
                    fn: Any = callback,
                    handler_name: str = name,
                ) -> None:
                    self._invoke_ui(f"hotkey.{handler_name}", fn)

                self.hotkeys.register(hotkey, invoke)
            except (ValueError, OSError, RuntimeError) as exc:
                failures.append(f"{name}: {exc}")
                if strict:
                    break
        if failures:
            if strict:
                self.hotkeys.unregister_all()
            self.tray.show_message("Горячая клавиша недоступна", "; ".join(failures), critical=True)
            return False
        return True

    @staticmethod
    def _shortcut_conflicts(specification: str, reference: object) -> bool:
        try:
            shortcut = parse_hotkey(specification, identifier=4, name="comparison")
        except ValueError:
            return False
        return (shortcut.virtual_key, shortcut.modifiers) == (reference.virtual_key, reference.modifiers)

    def _insert_dashboard_result(self) -> None:
        state = self.controller.snapshot()
        if state.full_transcript:
            self._submit_professional_text(state.full_transcript)

    def _copy_dashboard_result(self) -> None:
        self._run_background("voxtype-copy-dashboard",
                             lambda: self.signals.dashboard_copied.emit(self.controller.copy_full_transcript()))

    def show_command_palette(self) -> None:
        def choose_profile() -> None:
            profiles = tuple(item for item in self.controller.profiles.profiles() if self.controller.profile_allowed(item))
            titles = [item.name for item in profiles]
            if not titles:
                self.main.shell.notifications.notify("profiles-unavailable", "Нет доступных профилей", "warning")
                return
            name, accepted = QInputDialog.getItem(self.main, "Профиль диктовки", "Профиль", titles, editable=False)
            if accepted:
                self._run_background("voxtype-palette-profile",
                                     lambda: self.controller.profiles.set_default(profiles[titles.index(name)].id))

        entries = [
            ("Начать или остановить диктовку", self.main.dictation_button.click),
            ("Открыть историю", self.show_history), ("Выбрать профиль", choose_profile),
            ("Открыть словарь", lambda: self._navigate_desktop("dictionary")),
            ("Открыть модели", lambda: self._navigate_desktop("models")),
            ("Открыть настройки", self.show_settings),
            ("Скопировать последний результат",
             lambda: self._run_background("voxtype-palette-copy", self.controller.copy_full_transcript)),
            ("Открыть транскрипции", lambda: self._navigate_desktop("files")),
        ]
        CommandPalette(entries, self.main).exec()

    def _navigate_desktop(self, page: str) -> None:
        current = self.main.shell.current
        if current in {"settings", "models"} and page not in {"settings", "models"}:
            window = self.settings_window
            if not window.confirm_navigation():
                self.main.shell.show_page(current)
                return
            window.set_values(self.runtime.settings)
        if page in {"home", "dictation"}:
            self.main.shell.show_page(page)
            self.main.update_snapshot(self.controller.snapshot())
        elif page == "history":
            self.show_history()
        elif page in {"settings", "models"}:
            self.show_settings(page)
        else:
            self.show_professional_tools(page)

    def _embed_page(self, page: str, window: QWidget) -> None:
        shell = self.main.shell
        if shell.stack.indexOf(window) < 0:
            window.setWindowFlags(Qt.WindowType.Widget)
            window.setMinimumSize(0, 0)
        shell.install_page(page, window)
        shell.show_page(page)

    def show_professional_tools(self, page: str = "profiles") -> None:
        if self._professional_window is None:
            self._professional_window = ProfessionalWindow(self.controller, self.main)
            self._professional_window.insertion_requested.connect(self._submit_professional_text)
            self._start_professional_queue()
            if self._file_queue is not None:
                self._professional_window.bind_transcription_queue(self._file_queue)
        if not self._professional_window.open_section(page):
            self.main.shell.unavailable(page, "Раздел недоступен", "Нужный компонент отсутствует или отключён политикой.")
            return
        action = self._professional_window.section_action(page)
        if action is not None:
            self.main.shell.set_context_action(page, *action)
        self._embed_page(page, self._professional_window)
        if self._professional_window.transcription_panel is not None:
            self._professional_window.transcription_panel.refresh()

    def _start_professional_queue(self) -> None:
        if self._closing or self._file_queue is not None:
            return
        from importlib.util import find_spec

        if find_spec("av") is None:
            return
        from voxtype.asr.transcription_queue import FileTranscriptionQueue

        try:
            self._file_queue = FileTranscriptionQueue(
                self.runtime.session_repository.path, self.controller,
            )
        except (OSError, RuntimeError, sqlite3.Error):
            _LOGGER.exception("file_queue_initialization_failed")
            return
        self._professional_timer.start()

    def _poll_professional(self) -> None:
        if self._closing or self._file_queue is None:
            return
        try:
            if not self._interactive_asr.is_set():
                self._file_queue.tick()
            if self._professional_window is not None:
                panel = self._professional_window.transcription_panel
                if panel is not None:
                    panel.refresh()
        except Exception as exc:
            _LOGGER.warning("professional_queue_update_failed type=%s", type(exc).__name__)

    def _notify_desktop(self, key: str, text: str, kind: str = "info") -> None:
        if self.main.isVisible():
            self.main.shell.notifications.notify(key, text, kind)
        else:
            self.tray.show_message("VoxType", text, critical=kind == "error")

    def _submit_professional_text(self, text: str, session: int | None = None) -> None:
        def deliver() -> None:
            if not self.controller.insert_professional_text(text, session):
                self.signals.professional_delivery_failed.emit()

        self._run_background("voxtype-professional-insertion", deliver)

    def _on_workflow_action(self, action: str, title: str, text: str) -> None:
        if self._closing:
            return
        if action == "copy":
            QApplication.clipboard().setText(text)
            return
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QPushButton, QVBoxLayout

        dialog = QDialog(self.main)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(title)
        dialog.resize(630, 420)
        layout = QVBoxLayout(dialog)
        preview = QPlainTextEdit(text, dialog)
        layout.addWidget(preview)
        export_button = QPushButton("Экспорт TXT…", dialog)
        layout.addWidget(export_button)

        def save() -> None:
            filename, _ = QFileDialog.getSaveFileName(dialog, "Экспорт черновика", "VoxType.txt", "TXT (*.txt)")
            if filename:
                try:
                    Path(filename).write_text(preview.toPlainText(), encoding="utf-8")
                except OSError:
                    self.tray.show_message("VoxType", "Не удалось сохранить черновик", critical=True)

        export_button.clicked.connect(save)
        dialog.open()

    def _on_professional_action(
        self, session: int, action: VoiceAction, profile: DictationProfile
    ) -> None:
        if (self._closing or not self.controller.policy.allow_voice_commands
                or not self.controller.professional_session_is_current(session)):
            return
        from PySide6.QtWidgets import QMessageBox

        if action.confirmation_required and QMessageBox.question(
            self.main, "Голосовая команда", f"Выполнить команду «{action.trigger}»?"
        ) != QMessageBox.StandardButton.Yes:
            return
        if not self.controller.professional_session_is_current(session):
            return
        kind, payload = action.action_type, action.payload
        if kind == "CancelCurrentSession":
            self.controller.cancel_professional_session(session)
        elif kind == "OpenVoxTypePanel":
            if payload.get("panel") == "settings":
                self.show_settings()
            elif payload.get("panel") in {"dictionary", "snippets", "commands", "replacements"}:
                self.show_professional_tools(payload["panel"] if payload["panel"] in {"dictionary", "snippets"} else "profiles")
                if self._professional_window is not None:
                    self._professional_window.open_collection(payload["panel"])
        elif kind == "CopyLastResult":
            self._run_background("voxtype-professional-copy", self.controller.copy_previous_result)
        elif kind == "SwitchProfile":
            name = payload.get("name", "").casefold()
            profiles = self.controller.profiles
            selected = next((
                item for item in profiles.profiles() if self.controller.profile_allowed(item)
                and name in {item.id.casefold(), item.name.casefold()}
            ), None)
            if selected is not None:
                self._run_background("voxtype-switch-profile", lambda: profiles.set_default(selected.id))
        elif kind == "InsertSnippet" and self.controller.policy.allow_user_snippets:
            snippet = self.controller.professional_content.snapshot().find_snippet(
                payload.get("name", ""), profile
            )
            if snippet is not None:
                self.show_professional_tools()
                if self._professional_window is not None:
                    text = self._professional_window.render_snippet(snippet)
                    if text is not None:
                        self._submit_professional_text(text, session)
        elif kind in {"InsertLiteralText", "InsertParagraph", "InsertNewline"}:
            text = {"InsertParagraph": "\n\n", "InsertNewline": "\n"}.get(kind, payload.get("text", ""))
            self._submit_professional_text(text, session)

    def show_settings(self, page: str = "settings") -> None:
        if not self.main.isVisible():
            self._show_main()
        first = self._settings_window is None
        window = self.settings_window
        self._embed_page(page, window)
        if page == "models":
            window.open_models()
        if not first:
            return
        window.accepted.connect(lambda: self._navigate_desktop("home"))
        window.rejected.connect(lambda: self._navigate_desktop("home"))
        window.set_values(self.runtime.settings)
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in self.runtime.devices]
        window.set_audio_devices(devices, self.runtime.settings.audio.device_id)
        self._set_model_choices({})
        self._run_background("voxtype-settings-devices", self._scan_settings_devices)
        self._run_background("voxtype-settings-models", self._scan_settings_models)

    def _set_model_choices(self, installed: dict[str, int], *, preserve_selection: bool = False) -> None:
        models = [
            (f"small — быстро/баланс{' ✓' if 'small' in installed else ''}", "small"),
            (
                f"large-v3-turbo — максимум{' ✓' if 'large-v3-turbo' in installed else ''}",
                "large-v3-turbo",
            ),
        ]
        window = self.settings_window
        selected = (
            window.model_combo.currentData()
            if preserve_selection
            else self.runtime.settings.model.installed_model
        )
        window.set_models(models, selected)
        descriptions = {
            "small": "Модель для повседневной диктовки и работы на CPU.",
            "large-v3-turbo": "Режим максимальной точности для сложных текстов.",
        }
        policy = self.controller.policy
        cards = []
        for identifier in descriptions:
            size = installed.get(identifier)
            usable = policy.model_allowed(identifier)
            cards.append((identifier, descriptions[identifier],
                          f"{size / 1024**2:.0f} МиБ" if size is not None else "Размер не указан",
                          identifier in installed, identifier == self.controller.backend.model_id,
                          usable and not policy.enforce_offline_only, usable))
        window.set_model_cards(cards)
        window.gpu_experimental_check.setChecked(False)
        window.gpu_experimental_check.setEnabled(False)
        window.gpu_experimental_check.setToolTip(
            "В этой сборке используется проверенный CPU INT8; экспериментальный GPU отключён"
        )

    def _scan_settings_devices(self) -> None:
        try:
            devices = list_input_devices()
        except AudioBackendUnavailable:
            _LOGGER.warning("audio_device_discovery_unavailable")
            devices = ()
        self.signals.devices_ready.emit(devices)

    def _scan_settings_models(self) -> None:
        self.signals.models_ready.emit(
            {item.model_id: item.size_bytes for item in self.runtime.model_manager.list_installed()}
        )

    def _on_devices_ready(self, items: object) -> None:
        if not isinstance(items, (list, tuple)):
            return
        self.runtime.devices = tuple(items)
        selected_device = self.runtime.settings.audio.device_id
        self.controller.set_recording_availability(
            microphone=any(
                item.is_default if selected_device is None else item.index == selected_device
                for item in items
            )
        )
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in items]
        if self._settings_window is not None:
            self._settings_window.set_audio_devices(devices)
        if self.onboarding is not None:
            selected = self.onboarding.microphone_combo.currentData()
            self.onboarding.set_microphones(devices, selected)

    def _on_models_ready(self, installed: object) -> None:
        self._set_model_choices(
            installed if isinstance(installed, dict) else {},
            preserve_selection=True,
        )

    def _save_settings(self, values: object) -> None:
        if not isinstance(values, dict):
            return
        values = dict(values)
        policy = self.controller.policy
        if isinstance(values.get("privacy"), dict):
            privacy = dict(values["privacy"])
            if policy.disable_history:
                privacy.pop("save_history", None)
            if policy.disable_audio_storage:
                privacy.pop("save_audio", None)
            values["privacy"] = privacy
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
        theme_changed = updated.general.theme != previous.general.theme
        language_changed = updated.general.ui_language != previous.general.ui_language
        toggle_hotkey_changed = updated.hotkeys.toggle != previous.hotkeys.toggle
        hotkeys_changed = updated.hotkeys != previous.hotkeys
        audio_changed = updated.audio != previous.audio
        vad_changed = updated.audio.vad_sensitivity != previous.audio.vad_sensitivity
        self.runtime.settings = updated
        self.controller.settings = updated
        self.controller.vocabulary = vocabulary
        self.controller.target_manager.set_policy(TargetPolicy(updated.dictation.target_mode.value))
        self.controller.injection_service.strategy = updated.dictation.injection_mode.value
        self.controller.injection_service.paste_delay = (
            max(350, updated.dictation.insertion_delay_ms) / 1_000.0
        )
        self.main.set_minimize_to_tray(updated.general.minimize_to_tray)
        if language_changed:
            self.main.set_interface_language(updated.general.ui_language)
        if toggle_hotkey_changed:
            self.main.set_toggle_hotkey(updated.hotkeys.toggle)
        if theme_changed:
            apply_theme(self.application, ThemeMode(updated.general.theme))
        if updated.general.start_with_windows != previous_autostart:
            self.runtime.startup.set_enabled(updated.general.start_with_windows)
        if audio_changed:
            self._configure_capture_factory()
        if vad_changed:
            self._configure_segmenter_factory()
        if hotkeys_changed and not self._configure_hotkeys():
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
            if updated.general.start_with_windows != previous_autostart:
                self.runtime.startup.set_enabled(previous.general.start_with_windows)
            self.main.set_minimize_to_tray(previous.general.minimize_to_tray)
            if language_changed:
                self.main.set_interface_language(previous.general.ui_language)
            if toggle_hotkey_changed:
                self.main.set_toggle_hotkey(previous.hotkeys.toggle)
            if theme_changed:
                apply_theme(self.application, ThemeMode(previous.general.theme))
            if audio_changed:
                self._configure_capture_factory()
            if vad_changed:
                self._configure_segmenter_factory()
            self._configure_hotkeys(strict=False)
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
        # Discovery runs in the settings worker. Saving settings must never
        # synchronously wait for PortAudio on the Qt thread.
        devices = self.runtime.devices
        device = next(
            (item for item in devices if item.index == self.runtime.settings.audio.device_id),
            self.runtime.device,
        )
        selected_index = self.runtime.settings.audio.device_id
        profile = AudioPreprocessingProfile(self.runtime.settings.audio.preprocessing_profile.value)
        processing = replace(
            PreprocessorSettings.for_profile(profile),
            normalization_enabled=self.runtime.settings.audio.normalization,
            noise_suppression_enabled=self.runtime.settings.audio.noise_suppression,
        )

        def factory(queue: LosslessAudioQueue) -> AudioCaptureService:
            selected = resolve_recording_device(selected_index, self.runtime.devices)
            return AudioCaptureService(
                queue,
                AudioCaptureConfig(
                    device=selected.index,
                    input_sample_rate=choose_capture_rate(selected),
                    input_channels=choose_capture_channels(selected),
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
        if self.controller.policy.disable_history:
            self.main.shell.unavailable("history", "История отключена",
                                        "Организация запретила сохранение истории диктовки.")
            return
        if not self.main.isVisible():
            self._show_main()
        self.main.shell.set_context_action("history", "Обновить", self._refresh_history)
        self._embed_page("history", self.history_window)
        self._refresh_history()

    def _refresh_history(self) -> None:
        if self._closing or self._closed:
            return
        self._history_revision += 1
        if not self._history_loading:
            self._start_history_load()

    def _start_history_load(self) -> None:
        revision = self._history_revision
        self._history_loading = True
        self.history_window.set_loading(True)
        options = self.history_window.search_options()

        def read_history() -> None:
            try:
                sessions, more = self.runtime.session_repository.search(**options)
            except Exception:
                _LOGGER.exception("history_load_failed")
                self.signals.history_ready.emit(revision, None)
            else:
                self.signals.history_ready.emit(revision, (sessions, more))

        self._run_background("voxtype-history-load", read_history)

    def _on_history_ready(self, revision: int, result: tuple | None) -> None:
        self._history_loading = False
        if self._closing or self._closed:
            self.history_window.set_loading(False)
            return
        if revision != self._history_revision:
            self._start_history_load()
            return
        self.history_window.set_loading(False)
        if result is None:
            self.tray.show_message("VoxType", "Не удалось загрузить историю", critical=True)
            return
        sessions, more = result
        self.history_window.set_page_available(more)
        self.history_window.set_sessions(list(sessions))

    def _delete_history_session(self, session_id: str) -> None:
        if self.runtime.session_repository.delete(session_id):
            _remove_owned_path(
                self.runtime.data_directory / "audio" / f"{session_id}.wav",
                self.runtime.data_directory,
            )
            _remove_owned_path(self.controller.spool_root / session_id, self.controller.spool_root)
            self.history_window.remove_session(session_id)
            self._refresh_history()

    def _clear_finished_history(self) -> None:
        try:
            self.runtime.session_repository.delete_all_finished()
        except Exception as exc:
            _LOGGER.warning("history_clear_failed type=%s", type(exc).__name__)
            self.tray.show_message("VoxType", "Не удалось очистить историю", critical=True)
        else:
            self._refresh_history()

    def _recover_history_session(self, session_id: str) -> None:
        def worker() -> None:
            try:
                text = self.controller.recover_session(session_id)
                self.signals.recovery_ready.emit(session_id, True, text)
            except Exception as exc:
                _LOGGER.exception("session_recovery_failed type=%s", type(exc).__name__)
                self.signals.recovery_ready.emit(
                    session_id, False, failure_message("session_recovery")
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
                _LOGGER.exception("diagnostics_failed type=%s", type(exc).__name__)
                report = {
                    "summary": failure_message("diagnostics"),
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
        started = time.perf_counter()
        wizard = OnboardingWizard(self.main)
        _LOGGER.info(
            "onboarding_constructed duration_ms=%.1f", (time.perf_counter() - started) * 1_000.0
        )
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
        _LOGGER.info("onboarding_shown total_ms=%.1f", (time.perf_counter() - started) * 1_000.0)
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
                selected = next(
                    (candidate for candidate in self.runtime.devices if candidate.index == device),
                    self.runtime.device,
                )
                capture_rate = choose_capture_rate(selected)
                silence = record_input(
                    selected,
                    2.5,
                    capture_rate,
                    progress=lambda elapsed: self.signals.calibration_progress.emit(
                        min(89, round(elapsed / 7.5 * 90)),
                        "Пожалуйста, помолчите 2–3 секунды.",
                    ),
                )
                speech = record_input(
                    selected,
                    5.0,
                    capture_rate,
                    progress=lambda elapsed: self.signals.calibration_progress.emit(
                        min(89, round((2.5 + elapsed) / 7.5 * 90)),
                        "Теперь произнесите обычным голосом несколько предложений.",
                    ),
                )
                self.signals.calibration_progress.emit(90, "Анализирую измеренный сигнал…")
                result = analyze_calibration(
                    silence,
                    speech,
                    sample_rate=capture_rate,
                )
            except Exception as exc:
                _LOGGER.exception("calibration_capture_failed type=%s", type(exc).__name__)
                result = device_error_result(
                    "не удалось открыть выбранный микрофон", sample_rate=capture_rate
                )
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
        policy = self.controller.policy
        if policy.enforce_offline_only or not policy.model_allowed(spec.model_id):
            self.signals.model_ready.emit(False, "Загрузка модели запрещена политикой организации", spec.model_id)
            return
        if self.main.shell.current in {"settings", "models"} and self._settings_window is not None:
            if self._model_download_dialog is not None:
                self._model_download_dialog.show()
                self._model_download_dialog.raise_()
                return
            self._model_download_dialog = ModelDownloadDialog(spec.model_id, self.main)
            self._model_download_dialog.show()

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
                    False, failure_message("model_install"), spec.model_id
                )

        threading.Thread(target=worker, name="voxtype-model-install", daemon=True).start()

    def _on_model_progress(self, percent: int, message: str) -> None:
        if self._model_download_dialog is not None:
            self._model_download_dialog.update_progress(percent, message)
        if self._settings_window is not None:
            self._settings_window.set_model_download_progress(percent, message)
        if self.onboarding is not None:
            self.onboarding.set_model_progress(percent, message)

    def _on_model_ready(self, ready: bool, message: str, model_id: str) -> None:
        if self._model_download_dialog is not None:
            self._model_download_dialog.close()
            self._model_download_dialog.deleteLater()
            self._model_download_dialog = None
        if self._settings_window is not None:
            self._settings_window.set_model_download_progress(100)
            self._settings_window.set_diagnostics_status(message, ready)
        if ready:
            if self._settings_window is not None:
                self._run_background("voxtype-model-list-refresh", self._scan_settings_models)
        self._notify_desktop("model-result", message, "success" if ready else "error")
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
        if removed:
            self._run_background("voxtype-model-list-refresh", self._scan_settings_models)

    def _benchmark_model(self, model_id: str) -> None:
        selected = _coerce_asr_profile(model_id)
        if not self.controller.policy.model_allowed(DEFAULT_MODEL_SPECS[selected].model_id):
            self.settings_window.set_diagnostics_status("Модель запрещена политикой организации", False)
            return
        if self._interactive_asr.is_set() or (self._file_queue is not None and self._file_queue.busy):
            self.settings_window.set_diagnostics_status("Дождитесь завершения текущего распознавания", False)
            return
        self._interactive_asr.set()
        self.settings_window.set_diagnostics_status("Говорите обычным голосом 6 секунд…")

        def worker() -> None:
            try:
                seconds = 6
                sample_rate = 16_000
                capture_rate = choose_capture_rate(self.runtime.device)
                audio = record_input(self.runtime.device, seconds, capture_rate)
                if capture_rate != sample_rate:
                    audio = resample_audio(audio, capture_rate, sample_rate)
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
                _LOGGER.exception("model_benchmark_failed type=%s", type(exc).__name__)
                ok, message = False, failure_message("benchmark")
            finally:
                self._interactive_asr.clear()
            self.signals.settings_status.emit(message, ok)

        threading.Thread(target=worker, name="voxtype-model-benchmark", daemon=True).start()

    def _audio_test(self, device_text: str) -> None:
        def worker() -> None:
            try:
                selected = resolve_recording_device(
                    int(device_text) if device_text else None, self.runtime.devices
                )
                audio = record_input(selected, 2.0, choose_capture_rate(selected))
                rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
                clipping = float(np.mean(np.abs(audio) >= 0.98) * 100.0)
                self.signals.audio_level.emit(rms, clipping, "Говорите обычным голосом")
            except Exception as exc:
                _LOGGER.exception("microphone_test_failed type=%s", type(exc).__name__)
                self.signals.audio_level.emit(0.0, 0.0, failure_message("microphone_test"))

        threading.Thread(target=worker, name="voxtype-audio-test", daemon=True).start()

    def _delete_local_data(self) -> None:
        if self.controller.state.name != "IDLE" or (self._file_queue and self._file_queue.busy):
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
        if not self.controller.policy.model_allowed(self.controller.backend.model_id):
            self.signals.phrase_ready.emit(language, "Модель запрещена политикой организации", False)
            return
        if self._interactive_asr.is_set() or (self._file_queue is not None and self._file_queue.busy):
            self.signals.phrase_ready.emit(language, "Дождитесь завершения текущего распознавания", False)
            return
        self._interactive_asr.set()

        def worker() -> None:
            try:
                rate = choose_capture_rate(self.runtime.device)
                mono = record_input(self.runtime.device, 6.0, rate)
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
                policy = self.controller.policy
                fast_spec = DEFAULT_MODEL_SPECS[ASRProfile.FAST]
                if (rtf > 0.7 and selected is not ASRProfile.FAST
                        and policy.model_allowed(fast_spec.model_id)
                        and (not policy.enforce_offline_only
                             or self.runtime.model_manager.is_installed(fast_spec.model_id))):

                    def progress(done: int, total: int | None) -> None:
                        percent = int(done * 100 / total) if total else 0
                        self.signals.model_progress.emit(
                            percent, "Устанавливаю быстрый профиль по результату RTF"
                        )

                    if not self.runtime.model_manager.is_installed(fast_spec.model_id):
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
                _LOGGER.exception("phrase_test_failed type=%s", type(exc).__name__)
                text, ok = failure_message("phrase_test"), False
            finally:
                self._interactive_asr.clear()
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
            "general": {
                "onboarding_completed": True,
                "start_with_windows": bool(
                    values.get(
                        "start_with_windows", self.runtime.settings.general.start_with_windows
                    )
                ),
            },
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
        self._professional_timer.stop()
        if self._file_queue is not None:
            self._file_queue.begin_shutdown()
        self._heartbeat_timer.stop()
        self._heartbeat_stop.set()
        self.tray.show_message("VoxType", "Безопасно завершаю работу в фоне")

        def worker() -> None:
            try:
                if self._file_queue is not None:
                    self._file_queue.wait_for_worker(2)
                closed = self.runtime.close()
            except Exception:
                _LOGGER.exception("shutdown_worker_failed")
                closed = False
            self.signals.shutdown_ready.emit(closed)

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
                "VoxType", "Не удалось завершить работу; подробности в журнале. Повторите выход"
            )
            return
        self._closed = True
        if self._event_dispatcher is not None:
            self._event_dispatcher.awake.disconnect(self._dispatch_awake)
            self._event_dispatcher.aboutToBlock.disconnect(self._dispatch_done)
        self.hotkeys.close()
        if self._overlay is not None:
            self._overlay.close()
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


def run_desktop(runtime: RuntimeBundle | None = None, *, smoke_after_ms: int | None = None) -> int:
    started = time.perf_counter()
    existing = QApplication.instance()
    application = cast(QApplication, existing) if existing is not None else QApplication([])
    _LOGGER.info(
        "startup_stage name=qapplication_ready duration_ms=%.1f",
        (time.perf_counter() - started) * 1_000.0,
    )
    application.setApplicationName("VoxType")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("VoxType")
    shell = DesktopApplication(application, runtime or build_runtime())
    smoke_result = smoke_after_ms is None
    if smoke_after_ms is not None:

        def finish_smoke() -> None:
            nonlocal smoke_result
            if application.platformName() != "offscreen":
                central = shell.main.centralWidget()
                pixmap = shell.main.screen().grabWindow(int(shell.main.winId()))
                picture = pixmap.toImage()
                colors = {
                    picture.pixel(x, y) & 0xFFFFFF
                    for y in range(0, picture.height(), max(1, picture.height() // 24))
                    for x in range(0, picture.width(), max(1, picture.width() // 32))
                }
                smoke_result = bool(
                    shell.main._first_content_paint_seen
                    and central is not None
                    and central.width() > 0
                    and central.height() > 0
                    and len(colors) >= 10
                )
                _LOGGER.info(
                    "ui_smoke_render ok=%s colors=%s size=%sx%s first_paint=%s",
                    smoke_result,
                    len(colors),
                    picture.width(),
                    picture.height(),
                    shell.main._first_content_paint_seen,
                )
            else:
                _LOGGER.info("ui_smoke_render skipped=offscreen")
            shell.quit()

        QTimer.singleShot(smoke_after_ms, finish_smoke)
    _LOGGER.info(
        "startup_stage name=shell_ready total_ms=%.1f", (time.perf_counter() - started) * 1_000.0
    )
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
        _LOGGER.info(
            "startup_stage name=event_loop_enter total_ms=%.1f",
            (time.perf_counter() - started) * 1_000.0,
        )
        exit_code = int(application.exec())
        return exit_code if exit_code else (0 if smoke_result else 3)
    finally:
        _LOGGER.info(
            "startup_stage name=event_loop_exit total_ms=%.1f",
            (time.perf_counter() - started) * 1_000.0,
        )
        sys.excepthook = previous_excepthook


__all__ = ["DesktopApplication", "run_desktop"]
