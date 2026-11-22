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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

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
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox, QWidget

from voxtype.asr.base import TranscriptionRequest
from voxtype.asr.benchmark import BenchmarkCase, benchmark_backend, save_benchmark_results
from voxtype.asr.model_manager import (
    DEFAULT_MODEL_SPECS,
    ModelInstallCancelled,
    ModelIntegrityState,
)
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
    match_input_device,
    resolve_recording_device,
)
from voxtype.audio.preprocessing import (
    PreprocessingProfile as AudioPreprocessingProfile,
)
from voxtype.audio.preprocessing import select_preprocessing
from voxtype.audio.recording import record_input
from voxtype.audio.recovery import AudioDeviceReconciler, RecoveryCoordinator, RecoveryState
from voxtype.audio.resampler import resample_audio
from voxtype.audio.spool import LosslessAudioQueue
from voxtype.core.compute_budget import MemoryPolicy, PerformanceProfile, ResourcePhase
from voxtype.core.gestures import DictationGestureController, DictationMode
from voxtype.core.models import AppPhase, AppReadiness, Language, RecognitionStatus
from voxtype.core.profiles import DictationProfile
from voxtype.monitoring.hardware import probe_hardware
from voxtype.monitoring.health import run_health_checks
from voxtype.monitoring.logging import configure_logging
from voxtype.monitoring.storage import measure_owned_paths
from voxtype.runtime import (
    RuntimeBundle,
    build_runtime,
    cache_directory_path,
    domain_profile,
    install_silero_vad,
)
from voxtype.sessions.backup import BackupManager
from voxtype.settings.defaults import create_default_settings
from voxtype.settings.schema import AppSettings
from voxtype.settings.updates import StagedUpdate, UpdateMetadataClient, UpdatesUnavailable
from voxtype.transcript.editing import RevisionSource, TranscriptRevision
from voxtype.transcript.professional import CommandResolution, VoiceAction
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
from voxtype.ui.desktop_shell import CommandPalette
from voxtype.ui.messages import failure_message
from voxtype.ui.models import AppStatus, UIState
from voxtype.ui.motion import AnimationCoordinator
from voxtype.ui.obsidian import ModelDownloadDialog
from voxtype.vad.segmenter import SegmentationSettings, StreamingSegmenter
from voxtype.vad.silero import SileroVAD
from voxtype.version import __version__, version_info
from voxtype.windows.device_events import DeviceEvent, WindowsSystemEventBridge
from voxtype.windows.foreground import capture_foreground
from voxtype.windows.foreground_events import ForegroundEvents
from voxtype.windows.hotkeys import GlobalHotkeyManager, parse_hotkey
from voxtype.windows.power_events import PowerEventCoordinator
from voxtype.windows.send_input import SendInputController
from voxtype.windows.single_instance import WM_VOXTYPE_RESTORE
from voxtype.windows.target_window import TargetPolicy

if TYPE_CHECKING:
    from voxtype.ui.professional import ProfessionalWindow


_LOGGER = logging.getLogger("voxtype.app")


class _WorkerSignals(QObject):
    startup_ready = Signal(object)
    startup_failed = Signal(str)
    professional_action = Signal(int, object, object, object)
    professional_delivery_failed = Signal()
    dashboard_copied = Signal(bool)
    dashboard_delivery_finished = Signal()
    result_target_selected = Signal(bool)
    workflow_action = Signal(str, str, str)
    snapshot_ready = Signal(object)
    state_changed = Signal()
    foreground_changed = Signal(object)
    input_focus_changed = Signal()
    system_event = Signal(object)
    hold_changed = Signal(bool)
    recovery_devices_ready = Signal(int, object)
    recovery_available = Signal()
    personal_import_preview = Signal(object, object, object)
    data_status = Signal(str, bool)
    update_checked = Signal(object, object)
    update_staged = Signal(object)
    file_queue_ready = Signal(object)
    file_queue_changed = Signal()
    settings_saved = Signal(object)
    model_progress = Signal(int, str)
    model_ready = Signal(bool, str, str)
    calibration_progress = Signal(int, str)
    calibration_ready = Signal(object)
    phrase_ready = Signal(str, str, bool)
    diagnostics_ready = Signal(object)
    settings_status = Signal(str, object)
    audio_level = Signal(float, float, str)
    recovery_ready = Signal(str, bool, str)
    draft_restored = Signal(str, str, object)
    history_ready = Signal(int, object)
    delivery_outbox_ready = Signal(object)
    pending_flush_ready = Signal()
    hardware_ready = Signal(str, str)
    devices_ready = Signal(object)
    models_ready = Signal(object)
    shutdown_ready = Signal(object)


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(
        self,
        manager: GlobalHotkeyManager,
        power_events: PowerEventCoordinator,
        shutdown_query: Any,
        restore_request: Any,
        system_events: WindowsSystemEventBridge,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.power_events = power_events
        self.shutdown_query = shutdown_query
        self.restore_request = restore_request
        self.system_events = system_events

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
            self.system_events.handle_message(int(native.message), int(native.wParam))
            if int(native.message) == WM_VOXTYPE_RESTORE:
                self.restore_request()
                return True, 1
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


class _StartupRestoreFilter(QAbstractNativeEventFilter):
    def __init__(self, main: MainWindow) -> None:
        super().__init__()
        self._main = main

    def nativeEventFilter(self, event_type, message):
        from ctypes import wintypes

        if b"windows" in bytes(event_type):
            native = wintypes.MSG.from_address(int(message))
            if int(native.message) == WM_VOXTYPE_RESTORE:
                QTimer.singleShot(0, self._main, self._show)
                return True, 1
        return False, 0

    def _show(self) -> None:
        self._main.showNormal()
        self._main.raise_()
        self._main.activateWindow()


class DesktopApplication:
    """Own Qt views and adapt them to the Qt-independent controller."""

    def __init__(
        self, application: QApplication, runtime: RuntimeBundle, *, main: MainWindow | None = None
    ) -> None:
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
        self.main = main or MainWindow(
            None,
            interface_language=runtime.settings.general.ui_language,
            minimize_to_tray=runtime.settings.general.minimize_to_tray,
        )
        if main is not None:
            self.main.set_interface_language(runtime.settings.general.ui_language)
            self.main.set_minimize_to_tray(runtime.settings.general.minimize_to_tray)
        if os.environ.get("VOXTYPE_DATA_DIRECTORY", "").strip():
            geometry = QSettings(str(runtime.data_directory / "ui.ini"), QSettings.Format.IniFormat)
        else:
            geometry = QSettings("VoxType", "VoxType")
        self.main.set_geometry_store(geometry)
        self._view_preferences = geometry
        self.application.setProperty(
            "voxtypeReduceMotion", geometry.value("ui/reduceMotion", False, type=bool)
        )
        self._model_download_dialog: ModelDownloadDialog | None = None
        self._model_download_cancel: threading.Event | None = None
        self._downloading_model: str | None = None
        self._failed_model: str | None = None
        self._installed_model_sizes: dict[str, int] = {}
        self._model_integrity: dict[str, ModelIntegrityState] = {}
        self._open_palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self.main)
        self._open_palette_shortcut.activated.connect(self.show_command_palette)
        stage("main_window_constructed")
        self._overlay: DictationOverlay | None = None
        self.tray = TrayController(None)
        self._settings_window: SettingsWindow | None = None
        self._audio_test_cancel: threading.Event | None = None
        self._professional_window: ProfessionalWindow | None = None
        self._file_queue = None
        self._file_queue_starting = False
        self._pending_dictation_target = None
        self._dashboard_delivery_pending = False
        self._manual_delivery_epoch = 0
        self._scratchpad_session: int | None = None
        self._scratchpad_voice_edit = False
        self._interactive_asr = threading.Event()
        self._professional_timer = QTimer(self.main, interval=750)
        self._professional_timer.timeout.connect(self._poll_professional)
        QTimer.singleShot(1100, self._start_professional_queue)
        self._history_window: HistoryWindow | None = None
        self._diagnostics_window: DiagnosticsWindow | None = None
        stage("auxiliary_widgets_constructed")
        self.onboarding: OnboardingWizard | None = None
        self._practice: tuple[str, int] | None = None
        self._practice_timer = QTimer(self.main, interval=6000, singleShot=True)
        self._practice_timer.timeout.connect(self._stop_practice)
        self.hotkeys = GlobalHotkeyManager(hwnd=0)
        self._gestures = DictationGestureController(self.controller)
        self._system_sleeping = False
        self._system_locked = False
        self._recovery = RecoveryCoordinator()
        self._recovery_scanning = False
        self._recovery_timer = QTimer(self.main, singleShot=True)
        self._recovery_timer.timeout.connect(self._scan_recovery_devices)
        self.system_events = WindowsSystemEventBridge(self.signals.system_event.emit)
        self.power_events = PowerEventCoordinator(
            on_suspend=lambda: self.signals.system_event.emit("suspend"),
            on_resume=lambda: self.signals.system_event.emit("resume"),
        )
        self.hotkey_filter = _HotkeyEventFilter(
            self.hotkeys,
            self.power_events,
            self.close,
            lambda: QTimer.singleShot(0, self._show_main),
            self.system_events,
        )
        self.timer = QTimer(self.main)
        self.timer.setInterval(250)
        self._idle_model_timer = QTimer(self.main, singleShot=True)
        self._idle_model_timer.timeout.connect(self._release_idle_models)
        self._idle_model_timeout_ms = 0
        self._last_state = "STARTUP"
        self._last_rendered_snapshot: object | None = None
        self._header_profile_id: str | None = None
        self._result_dismissed = False
        self._last_pending_flush = 0.0
        self._last_target_sample = 0.0
        self._pending_flush_running = False
        self._history_revision = 0
        self._history_recovery = False
        self._backups = BackupManager(
            runtime.session_repository.path, runtime.data_directory / "backups"
        )
        self._backup_running = False
        self._update_busy = False
        self._update_cancel: threading.Event | None = None
        self._update_dialog: QMessageBox | None = None
        self.installer_handoff: StagedUpdate | None = None
        self._history_loading = False
        self._deferred_settings: dict[str, object] = {}
        self._settings_writer: threading.Thread | None = None
        self._settings_write_running = False
        self._background_lock = threading.Lock()
        self._background_workers: set[threading.Thread] = set()
        self._closed = False
        self._closing = False
        self._foreground_events = ForegroundEvents(
            self.signals.foreground_changed.emit,
            focus_notify=self.signals.input_focus_changed.emit,
        )
        self._disconnect_state_listener = self.controller.state_machine.subscribe(
            lambda _transition: self.signals.state_changed.emit()
        )
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
        self._apply_motion_profile()
        stage("stylesheet_applied")
        self.application.setWindowIcon(create_app_icon())
        self.application.setQuitOnLastWindowClosed(False)
        # A native show/tray call can stall before the Qt event loop starts.
        # Start the watchdog before exposing the HWND so it can capture that stack.
        watchdog = os.environ.get("VOXTYPE_UI_WATCHDOG", "").strip() == "1"
        if watchdog:
            self._heartbeat_last = time.monotonic()
            threading.Thread(
                target=self._heartbeat_monitor, name="voxtype-ui-watchdog", daemon=True
            ).start()
        self._observe_foreground()
        self.main.show()
        stage("main_window_shown")
        self._update_mode_indicator()
        hwnd = int(self.main.winId())
        self.controller.target_manager.register_own_window(hwnd)
        QTimer.singleShot(
            0, self.main, lambda: self.system_events.start(hwnd) if not self._closing else None
        )
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
        if watchdog:
            self._heartbeat_last = time.monotonic()
            self._heartbeat_timer.start()
        stage("qt_timers_started")
        if not runtime.settings.general.onboarding_completed:
            QTimer.singleShot(100, self.show_onboarding)
        if runtime.restore_status:
            self._show_data_status(
                "Резервная копия восстановлена."
                if runtime.restore_status == "restored"
                else "Восстановление базы не завершилось. Подробности в техническом журнале.",
                runtime.restore_status == "restored",
            )
        if not self.controller.policy.disable_history:
            self._run_background("voxtype-recovery-discovery", self._check_recovery)

    def _connect(self) -> None:
        self.main.hidden_to_tray.connect(self._poll)
        self.main.dictation_toggle_requested.connect(
            lambda: self._invoke_ui("main.toggle_dictation", self._toggle_interactive_dictation)
        )
        self.main.pause_toggle_requested.connect(
            lambda: self._invoke_ui("main.toggle_pause", self.controller.toggle_pause)
        )
        self.main.language_change_requested.connect(
            lambda language: self._invoke_ui(
                "main.switch_language", lambda: self.controller.set_language(Language(language))
            )
        )
        self.tray.dictation_toggle_requested.connect(
            lambda: self._invoke_ui("tray.toggle_dictation", self._toggle_interactive_dictation)
        )
        self.tray.pause_toggle_requested.connect(
            lambda: self._invoke_ui("tray.toggle_pause", self.controller.toggle_pause)
        )
        self.tray.language_change_requested.connect(
            lambda language: self._invoke_ui(
                "tray.switch_language", lambda: self.controller.set_language(Language(language))
            )
        )
        self.main.settings_requested.connect(self.show_settings)
        self.main.shell.page_requested.connect(self._navigate_desktop)
        self.main.shell.about_requested.connect(self.show_diagnostics)
        self.main.shell.palette_requested.connect(self.show_command_palette)
        self.main.copy_result_requested.connect(self._copy_dashboard_result)
        self.signals.dashboard_copied.connect(
            lambda copied: self._notify_desktop(
                "copy-transcript",
                "Текст скопирован" if copied else "Не удалось скопировать текст",
                "success" if copied else "warning",
            )
        )
        self.main.insert_result_requested.connect(self._insert_dashboard_result)
        self.main.clear_result_requested.connect(self._clear_dashboard_result)
        self.main.select_target_requested.connect(self._select_dashboard_target)
        self.signals.result_target_selected.connect(
            lambda selected: self._notify_desktop(
                "result-target",
                "Целевое окно выбрано. Можно повторить вставку."
                if selected
                else "Не удалось выбрать окно. Оставьте нужное приложение на переднем плане.",
                "success" if selected else "warning",
            )
        )
        self.main.cancel_processing_requested.connect(
            lambda: self._invoke_ui("main.cancel", self.controller.cancel_processing)
        )
        self.main.professional_requested.connect(self.show_professional_tools)
        self.main.edit_result_requested.connect(self._open_recognition_editor)
        self.main.copy_pending_requested.connect(self._copy_pending_result)
        self.main.exit_requested.connect(self.quit)
        self.main.history_requested.connect(self.show_history)
        self.main.diagnostics_requested.connect(self.show_diagnostics)
        self.main.onboarding_requested.connect(self.show_onboarding)
        self.main.retry_requested.connect(
            lambda: self._invoke_ui("main.retry", self._retry_recording)
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
        self.signals.draft_restored.connect(
            self._open_restored_draft, Qt.ConnectionType.QueuedConnection
        )
        self.signals.professional_action.connect(
            self._on_professional_action, Qt.ConnectionType.QueuedConnection
        )
        self.signals.professional_delivery_failed.connect(
            lambda: self.tray.show_message(
                "VoxType", "Не удалось отправить текст в целевое окно", critical=True
            )
        )
        self.signals.workflow_action.connect(
            self._on_workflow_action, Qt.ConnectionType.QueuedConnection
        )
        self.signals.snapshot_ready.connect(self._update_views_if_changed)
        self.signals.state_changed.connect(self._poll, Qt.ConnectionType.QueuedConnection)
        self.signals.file_queue_ready.connect(
            self._on_file_queue_ready,
            Qt.ConnectionType.QueuedConnection,
        )
        self.signals.file_queue_changed.connect(
            self._poll_professional,
            Qt.ConnectionType.QueuedConnection,
        )
        self.signals.input_focus_changed.connect(
            self._schedule_overlay_caret, Qt.ConnectionType.QueuedConnection
        )
        self.signals.foreground_changed.connect(
            self._observe_foreground,
            Qt.ConnectionType.QueuedConnection,
        )
        self.signals.history_ready.connect(
            self._on_history_ready, Qt.ConnectionType.QueuedConnection
        )
        self.signals.delivery_outbox_ready.connect(
            self._show_delivery_outbox, Qt.ConnectionType.QueuedConnection
        )
        self.signals.settings_saved.connect(
            self._finish_settings_write,
            Qt.ConnectionType.QueuedConnection,
        )
        self.signals.system_event.connect(self._on_system_event, Qt.ConnectionType.QueuedConnection)
        self.signals.hold_changed.connect(self._on_hold_changed, Qt.ConnectionType.QueuedConnection)
        self.signals.dashboard_delivery_finished.connect(
            self._finish_dashboard_delivery, Qt.ConnectionType.QueuedConnection
        )
        self.signals.recovery_available.connect(
            lambda: self._notify_desktop(
                "recovery-center", "Есть данные для восстановления. Откройте Центр восстановления."
            ),
            Qt.ConnectionType.QueuedConnection,
        )
        self.signals.personal_import_preview.connect(
            self._confirm_personal_import, Qt.ConnectionType.QueuedConnection
        )
        self.signals.data_status.connect(self._show_data_status, Qt.ConnectionType.QueuedConnection)
        self.signals.update_checked.connect(
            self._confirm_update_download, Qt.ConnectionType.QueuedConnection
        )
        self.signals.update_staged.connect(
            self._confirm_update_install, Qt.ConnectionType.QueuedConnection
        )
        self.signals.recovery_devices_ready.connect(
            self._apply_recovered_devices,
            Qt.ConnectionType.QueuedConnection,
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
            window.release_models_button.clicked.connect(
                lambda: self._release_idle_models(notify=True)
            )
            window.audio_test_requested.connect(self._audio_test)
            window.audio_view_hidden.connect(self._stop_audio_test)
            window.delete_local_data_requested.connect(self._delete_local_data)
            window.recovery_center_requested.connect(self.show_recovery_center)
            window.backup_requested.connect(self._create_backup)
            window.restore_backup_requested.connect(self._choose_backup_restore)
            window.export_personal_requested.connect(self._export_personal_data)
            window.import_personal_requested.connect(self._import_personal_data)
            window.export_archive_requested.connect(self._export_personal_archive)
            window.updates_requested.connect(self._check_updates)
            window.input_compatibility_requested.connect(self._open_input_compatibility)
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
            window.outbox_requested.connect(self._load_delivery_outbox)
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
            window.resources_requested.connect(self._show_live_resource_counters)
            window.updates_requested.connect(self._check_updates)
            window.self_test_requested.connect(self._refresh_diagnostics)
            window.open_log_requested.connect(self._open_log)
            window.export_requested.connect(self._export_diagnostics)
        return window

    def _invoke_ui(self, handler: str, callback: Any) -> None:
        started = time.monotonic()
        state = self.controller.state.name
        try:
            if not self._closing:
                callback()
        finally:
            self.signals.state_changed.emit()
            elapsed_ms = (time.monotonic() - started) * 1_000.0
            if elapsed_ms > 200.0:
                _LOGGER.warning(
                    "UI_BLOCK handler=%s state=%s elapsed_ms=%.1f",
                    handler,
                    state,
                    elapsed_ms,
                )

    def _run_background(self, name: str, callback: Any, *, finish_on_close: bool = False) -> bool:
        if self._closing or self._closed:
            return False

        def run() -> None:
            try:
                if not self._closing or finish_on_close:
                    callback()
            except Exception as exc:
                _LOGGER.warning(
                    "background_operation_failed name=%s kind=%s", name, type(exc).__name__
                )
            finally:
                with self._background_lock:
                    self._background_workers.discard(threading.current_thread())
                self.signals.state_changed.emit()

        worker = threading.Thread(target=run, name=name, daemon=True)
        with self._background_lock:
            saturated = len(self._background_workers) >= 16
            if not saturated:
                self._background_workers.add(worker)
                try:
                    worker.start()
                except BaseException:
                    self._background_workers.discard(worker)
                    raise
        if saturated:
            self._notify_desktop(
                "background-busy", "Дождитесь завершения фоновых операций", "warning"
            )
        return not saturated

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
        self._last_rendered_snapshot = None
        self._poll()

    def _dismiss_result(self) -> None:
        self._result_dismissed = True
        if self._overlay is not None:
            self._overlay.hide()

    def _update_views_if_changed(self, snapshot: object) -> None:
        profiles = self.controller.profiles
        active_profile = getattr(self.controller, "_session_profile", None)
        state = getattr(snapshot, "state", "")
        active = state in {
            "STARTING",
            "MODEL_LOADING",
            "LISTENING",
            "SPEECH_DETECTED",
            "TRANSCRIBING",
            "PAUSED",
            "PAUSED_TARGET_LOST",
            "FINISHING",
        }
        profile_id = (
            active_profile.id if active and active_profile is not None else profiles.default_id
        )
        if profile_id != self._header_profile_id:
            chosen = (
                active_profile
                if active and active_profile is not None
                else next(
                    (profile for profile in profiles.profiles() if profile.id == profile_id),
                    None,
                )
            )
            self.main.set_profile_name(chosen.name if chosen is not None else "")
            self._header_profile_id = profile_id
        # Idle and completed snapshots are often identical for many polls.
        # Rewriting every Qt property also repaints the overlay and tray.
        if snapshot == self._last_rendered_snapshot:
            return
        self._observe_practice(snapshot)
        self._observe_scratchpad(snapshot)
        if self._settings_window is not None and self._settings_window.isVisible():
            self._refresh_model_cards(snapshot.readiness.phase.value)
            self._settings_window.update_audio_quality(
                snapshot.audio_quality, snapshot.microphone_name
            )
        if self.main.isVisible():
            self.main.update_snapshot(snapshot)
        self.tray.update_snapshot(snapshot)
        if self._overlay is not None:
            self._overlay.update_snapshot(snapshot)
        self._last_rendered_snapshot = snapshot

    def _toggle_interactive_dictation(self) -> None:
        if self._settings_write_running and self.controller.resource_phase is ResourcePhase.READY:
            self._notify_desktop("settings-pending", "Сохраняю параметры диктовки")
            return
        if self._pending_dictation_target is not None:
            self._pending_dictation_target = None
            self._notify_desktop("dictation-waiting", "Ожидание диктовки отменено")
        elif self._file_queue is not None and self._file_queue.busy:
            target = capture_foreground()
            if target is not None:
                self._pending_dictation_target = target
                self._notify_desktop(
                    "dictation-waiting",
                    "Диктовка начнётся после текущего файла, если окно останется активным",
                )
            else:
                self._notify_desktop("dictation-waiting", "Выберите окно для диктовки", "warning")
        else:
            self.controller.toggle_dictation()

    def _schedule_overlay_caret(self) -> None:
        if (
            self._overlay is not None
            and self._overlay.isVisible()
            and self.runtime.settings.dictation.caret_overlay
            and not self._closing
        ):
            QTimer.singleShot(350, self.main, self._update_overlay_caret)

    def _update_overlay_caret(self) -> None:
        if (
            not self._closing
            and self._overlay is not None
            and self._overlay.isVisible()
            and self.runtime.settings.dictation.caret_overlay
            and self.controller.resource_phase is ResourcePhase.ACTIVE
        ):
            self._overlay.place_near_caret(self.controller.current_caret())

    def _observe_foreground(self, hwnd: int = 0) -> None:
        if self._closing:
            return
        if (
            hwnd
            and self._pending_dictation_target is not None
            and hwnd != self._pending_dictation_target.hwnd
        ):
            self._pending_dictation_target = None
            self._notify_desktop(
                "dictation-waiting", "Активное окно изменилось. Запустите диктовку снова", "warning"
            )
        manager = self.controller.target_manager
        with suppress(OSError, RuntimeError):
            if (
                self.controller.resource_phase is ResourcePhase.READY
                and not self.controller.injection_service.pending_fragments
            ):
                manager.remember_foreground_target(hwnd)
            elif manager.policy is TargetPolicy.FOLLOW_FOCUS:
                manager.observe_foreground(hwnd)

    def _apply_motion_profile(self) -> None:
        eco = (
            PerformanceProfile.resolve(self.runtime.settings.model.power_mode)
            is PerformanceProfile.ECO
        )
        self.application.setProperty("voxtypeEcoMotion", eco)
        AnimationCoordinator.instance().preference_changed()

    def _release_idle_models(self, *, notify: bool = False) -> None:
        def release() -> None:
            released = self.controller.release_idle_models()
            if notify:
                self.signals.settings_status.emit(
                    "Модель выгружена из памяти"
                    if released
                    else "Дождитесь завершения текущей операции",
                    released,
                )
            self.signals.state_changed.emit()

        self._run_background("voxtype-model-release", release)

    def _update_idle_model_timer(self, phase: ResourcePhase) -> None:
        model = self.runtime.settings.model
        eligible = (
            phase is ResourcePhase.READY
            and model.memory_policy == MemoryPolicy.UNLOAD_AFTER_IDLE
            and self.controller.any_asr_model_loaded
        )
        if not eligible:
            self._idle_model_timer.stop()
            return
        timeout_ms = min(120, max(1, model.idle_unload_minutes)) * 60_000
        if not self._idle_model_timer.isActive() or self._idle_model_timeout_ms != timeout_ms:
            self._idle_model_timeout_ms = timeout_ms
            self._idle_model_timer.start(timeout_ms)

    def _poll(self) -> None:
        if self._closing or self._closed:
            return
        poll_started = time.perf_counter()
        now = time.monotonic()
        if not self._foreground_events.active and now - self._last_target_sample >= 1.0:
            self._last_target_sample = now
            self._observe_foreground()
        target_done = time.perf_counter()
        snapshot = self.controller.snapshot()
        if (
            self._deferred_settings
            and not self._settings_write_running
            and self.controller.can_change_runtime_settings()
        ):
            changes, self._deferred_settings = self._deferred_settings, {}
            self._save_settings(changes)
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
            privacy = self.runtime.settings.privacy
            if not self.controller.policy.disable_history and (
                privacy.save_history or privacy.recover_unsaved_drafts
            ):
                self._create_backup(automatic=True)
            self._result_dismissed = bool(
                self.runtime.settings.dictation.auto_hide_completed_result
            )
        elif snapshot.state in {"STARTING", "MODEL_LOADING", "LISTENING"}:
            self._result_dismissed = False
        if (
            active
            and not (self._system_locked or self._system_sleeping)
            and self.runtime.settings.general.show_overlay
            and not (snapshot.state == "COMPLETED" and self._result_dismissed)
        ):
            overlay = self._overlay
            if overlay is None:
                overlay = self.overlay
                overlay.update_snapshot(snapshot)
            if not overlay.isVisible() and self.runtime.settings.dictation.caret_overlay:
                overlay.place_near_caret(self.controller.input_caret)
            overlay.show_without_activating()
        else:
            if self._overlay is not None:
                self._overlay.hide()
        if snapshot.state != self._last_state and snapshot.state == "ERROR_RECOVERABLE":
            message = snapshot.error_message or snapshot.status_message
            if self.main.isVisible():
                self.main.shell.notifications.notify(
                    "dictation-error",
                    "Диктовка требует внимания",
                    "error",
                    details=message,
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
            snapshot.state == "PAUSED_TARGET_LOST"
            and snapshot.delivery_status not in {"copy_required", "no_result"}
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
        phase = self.controller.resource_phase
        self._update_idle_model_timer(phase)
        if snapshot.readiness.phase is AppPhase.RECORDING:
            interval = 34
        elif phase in {ResourcePhase.ACTIVE, ResourcePhase.QUIESCING, ResourcePhase.INITIALIZING}:
            interval = 250
        else:
            interval = 500 if self.main.isVisible() else 1000
        poll_needed = (
            self.main.isVisible()
            or not self._foreground_events.active
            or phase is not ResourcePhase.READY
            or self._deferred_settings
            or self._pending_flush_running
        )
        if poll_needed:
            if not self.timer.isActive() or self.timer.interval() != interval:
                self.timer.start(interval)
        else:
            self.timer.stop()
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

    def _on_system_event(self, event: str | DeviceEvent) -> None:
        if self._closing:
            return
        if isinstance(event, DeviceEvent):
            if (
                not self._system_locked
                and not self._system_sleeping
                and self._recovery.state
                not in {
                    RecoveryState.INTERRUPTED,
                    RecoveryState.RECOVERING,
                }
            ):
                self._request_device_recovery()
            return
        if event in {"suspend", "resume"}:
            self._system_sleeping = event == "suspend"
        elif event in {"lock", "unlock"}:
            self._system_locked = event == "lock"
        if self._system_locked or self._system_sleeping:
            self._manual_delivery_epoch += 1
            self._recovery.restart()
            self._recovery_timer.stop()
            self.hotkeys.release_hold_state()
            self._stop_audio_test()
            self._pending_dictation_target = None
            self.controller.set_system_unavailable(True)
            self.controller.interrupt_dictation()
            if self._overlay is not None:
                self._overlay.hide()
            self._poll()
        else:
            self._request_device_recovery()

    def _retry_recording(self) -> None:
        if self.controller.snapshot().readiness.reason == "microphone_missing":
            self._request_device_recovery()
            return
        if self._recovery.state in {RecoveryState.NEEDS_USER_ACTION, RecoveryState.FAILED}:
            self._request_device_recovery()
        else:
            self.controller.restart_dictation()

    def _request_device_recovery(self) -> None:
        if self._system_locked or self._system_sleeping or self._closing:
            return
        self._recovery.restart()
        self._schedule_device_recovery()

    def _schedule_device_recovery(self) -> None:
        delay = self._recovery.next_delay_ms()
        if delay is None:
            self.controller.set_system_unavailable(False)
            self.controller.set_recording_availability(microphone=False)
            self._notify_desktop(
                "device-recovery",
                "Микрофон недоступен. Подключите его и нажмите «Повторить».",
                "warning",
            )
            self._poll()
        else:
            self._recovery_timer.start(delay)

    def _scan_recovery_devices(self) -> None:
        if self._closing or self._system_locked or self._system_sleeping:
            return
        if self._recovery_scanning:
            return
        generation = self._recovery.generation
        self._recovery.state = RecoveryState.RECOVERING
        self._recovery_scanning = True

        def scan() -> None:
            try:
                devices = list_input_devices(candidate_sample_rates=(16_000, 48_000))
            except Exception as exc:
                _LOGGER.warning("microphone_recovery_failed kind=%s", type(exc).__name__)
                devices = ()
            self.signals.recovery_devices_ready.emit(generation, devices)

        if not self._run_background("voxtype-device-recovery", scan):
            self._recovery_scanning = False
            self._schedule_device_recovery()

    def _apply_recovered_devices(self, generation: int, devices: tuple) -> None:
        self._recovery_scanning = False
        if self._closing or self._system_locked or self._system_sleeping:
            return
        if generation != self._recovery.generation:
            if not self._recovery_timer.isActive():
                self._recovery_timer.start(500)
            return
        audio = self.runtime.settings.audio
        candidate = AudioDeviceReconciler.match(devices, audio.device_id, audio.device_fingerprint)
        old = self.runtime.device
        if candidate is None or (
            audio.device_id is None and candidate.fingerprint != old.fingerprint
        ):
            self.controller.interrupt_dictation()
        if candidate is None:
            self.controller.set_recording_availability(microphone=False)
            self._schedule_device_recovery()
        else:
            self._on_devices_ready(devices)
            self._configure_capture_factory()
            self.controller.set_system_unavailable(False)
            self._recovery.state = RecoveryState.HEALTHY
            self._poll()

    def _on_hold_changed(self, pressed: bool) -> None:
        if not pressed or (not self._settings_write_running and not self._closing):
            self._gestures.hold_changed(pressed)

    def _configure_hotkeys(
        self,
        *,
        strict: bool = True,
        settings: object = None,
        mode: DictationMode | None = None,
    ) -> bool:
        values = settings if settings is not None else self.runtime.settings.hotkeys
        mode = mode or self.runtime.settings.dictation.mode
        definitions = (
            (values.language, "toggle_language", self.controller.switch_language),
            (values.pause, "toggle_pause", self.controller.toggle_pause),
        )
        if mode in {DictationMode.TOGGLE, DictationMode.AUTO_STOP}:
            definitions = (
                (values.toggle, "toggle_dictation", self._toggle_interactive_dictation),
                *definitions,
            )
        callbacks = {}
        for _specification, name, callback in definitions:
            callbacks[name] = lambda _hotkey, fn=callback, key=name: self._invoke_ui(
                f"hotkey.{key}", fn
            )
        hold = None
        try:
            requested = tuple(
                parse_hotkey(specification, identifier=index, name=name)
                for index, (specification, name, _callback) in enumerate(definitions, 1)
            )
            hold = (
                parse_hotkey(values.push_to_talk, identifier=5, name="push_to_talk")
                if mode == DictationMode.PUSH_TO_TALK
                else None
            )
            self.hotkeys.replace_gestures(
                requested, callbacks, hold, self.signals.hold_changed.emit
            )
        except (ValueError, OSError, RuntimeError) as exc:
            _LOGGER.warning("hotkey_configuration_failed kind=%s", type(exc).__name__)
            if not strict and not self.hotkeys.registered:
                for index, (specification, name, _callback) in enumerate(definitions, 1):
                    try:
                        item = parse_hotkey(specification, identifier=index, name=name)
                        self.hotkeys.register(item, callbacks[name])
                    except (ValueError, OSError, RuntimeError) as partial:
                        _LOGGER.warning(
                            "hotkey_registration_failed action=%s kind=%s",
                            name,
                            type(partial).__name__,
                        )
            self._notify_desktop("hotkey-conflict", failure_message("hotkey_conflict"), "warning")
            if strict:
                return False
        palette = parse_hotkey("Ctrl+K", identifier=4, name="command_palette")
        self._open_palette_shortcut.setEnabled(
            not any(
                (item.virtual_key, item.modifiers) == (palette.virtual_key, palette.modifiers)
                for item in (
                    *self.hotkeys.registered,
                    *((hold,) if hold and self.hotkeys.hold_active else ()),
                )
            )
        )
        return True

    def _update_mode_indicator(self) -> None:
        mode = self.runtime.settings.dictation.mode
        values = self.runtime.settings.hotkeys
        names = {
            DictationMode.TOGGLE: "По нажатию",
            DictationMode.PUSH_TO_TALK: "Пока удерживаете",
            DictationMode.AUTO_STOP: "Стоп по тишине",
            DictationMode.MANUAL: "Вручную",
        }
        shortcut = values.toggle if mode in {DictationMode.TOGGLE, DictationMode.AUTO_STOP} else ""
        if mode == DictationMode.PUSH_TO_TALK:
            shortcut = values.push_to_talk if self.hotkeys.hold_active else ""
            if not shortcut:
                names[mode] = "Удержание недоступно"
        self.main.set_toggle_hotkey(shortcut, names[mode])

    def _open_input_compatibility(self) -> None:
        self.show_professional_tools()
        if self._professional_window is not None:
            self._professional_window.edit_input_compatibility()

    def _finish_dashboard_delivery(self) -> None:
        self._dashboard_delivery_pending = False

    def _confirm_multiline_delivery(self, text: str) -> bool:
        if not any(character in text for character in "\n\r\u2028\u2029"):
            return True
        return (
            QMessageBox.question(
                self.main,
                "Многострочная вставка",
                "Отправить текст с переносами? Терминал может интерпретировать вставленные строки как команды.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _schedule_manual_delivery(self, send: Callable[[], None]) -> None:
        if (
            self._dashboard_delivery_pending
            or self._closing
            or self._system_locked
            or self._system_sleeping
        ):
            return
        self._dashboard_delivery_pending = True
        epoch = self._manual_delivery_epoch
        self._notify_desktop(
            "manual-delivery", "Переключитесь в нужное поле в течение трёх секунд."
        )

        def deliver() -> None:
            if self._closing or epoch != self._manual_delivery_epoch:
                self._finish_dashboard_delivery()
                return
            send()

        QTimer.singleShot(3000, self.main, deliver)

    def _insert_dashboard_result(self) -> None:
        if self._dashboard_delivery_pending or self._closing:
            return
        state = self.controller.snapshot()
        text = self.main.result_text()
        if not text.strip() or not self._confirm_multiline_delivery(text):
            return
        if self.controller.injection_service.pending_fragments and (text != state.full_transcript):
            self._notify_desktop(
                "pending-edit",
                "Есть невставленные фрагменты. Скопируйте правку вручную.",
                "warning",
            )
            return
        recognition = state.recognition_result
        session = recognition.session_id if recognition is not None else None
        retry_pending = bool(self.controller.injection_service.pending_fragments)

        def deliver() -> None:
            updated = self.controller.snapshot()
            current = updated.recognition_result
            if (
                updated.full_transcript != state.full_transcript
                or (current.session_id if current else None) != session
            ):
                self._finish_dashboard_delivery()
                return

            def send() -> None:
                try:
                    if retry_pending:
                        if not self.controller.retry_completed_insertion(multiline_confirmed=True):
                            self.signals.professional_delivery_failed.emit()
                    elif not self.controller.insert_professional_text(
                        text, multiline_confirmed=True
                    ):
                        self.signals.professional_delivery_failed.emit()
                finally:
                    self.signals.dashboard_delivery_finished.emit()

            if not self._run_background("voxtype-dashboard-delivery", send):
                self._finish_dashboard_delivery()

        self._schedule_manual_delivery(deliver)

    def _copy_pending_result(self) -> None:
        self._run_background(
            "voxtype-copy-pending",
            lambda: self.signals.dashboard_copied.emit(self.controller.copy_pending_result()),
        )

    def _copy_dashboard_result(self) -> None:
        text = self.main.result_text()
        self._run_background(
            "voxtype-copy-dashboard",
            lambda: self.signals.dashboard_copied.emit(
                self.controller.injection_service.copy_text_to_clipboard(text)
            ),
        )

    def _clear_dashboard_result(self) -> None:
        if self.controller.clear_last_result():
            self.main.result_editor.clear()
        else:
            self._notify_desktop(
                "result-pending", "Сначала доставьте ожидающий вставки текст.", "warning"
            )

    def _select_dashboard_target(self) -> None:
        self._notify_desktop("choose-target", "Переключитесь в нужное окно в течение трёх секунд.")
        QTimer.singleShot(
            3000,
            self.main,
            lambda: self._run_background(
                "voxtype-result-target",
                lambda: self.signals.result_target_selected.emit(
                    self.controller.select_result_target()
                ),
            ),
        )

    def show_command_palette(self) -> None:
        def choose_profile() -> None:
            profiles = tuple(
                item
                for item in self.controller.profiles.profiles()
                if self.controller.profile_allowed(item)
            )
            titles = [item.name for item in profiles]
            if not titles:
                self.main.shell.notifications.notify(
                    "profiles-unavailable", "Нет доступных профилей", "warning"
                )
                return
            name, accepted = QInputDialog.getItem(
                self.main, "Профиль диктовки", "Профиль", titles, editable=False
            )
            if accepted:
                self._run_background(
                    "voxtype-palette-profile",
                    lambda: self.controller.profiles.set_default(profiles[titles.index(name)].id),
                )

        entries = [
            ("Начать или остановить диктовку", self.main.dictation_button.click),
            ("Открыть историю", self.show_history),
            ("Выбрать профиль", choose_profile),
            ("Открыть словарь", lambda: self._navigate_desktop("dictionary")),
            ("Открыть модели", lambda: self._navigate_desktop("models")),
            ("Открыть настройки", self.show_settings),
            (
                "Скопировать последний результат",
                lambda: self._run_background(
                    "voxtype-palette-copy", self.controller.copy_full_transcript
                ),
            ),
            ("Открыть транскрипции", lambda: self._navigate_desktop("files")),
            ("Открыть блокнот диктовки", self._open_scratchpad),
            (
                "Запись: удерживать клавиши",
                lambda: self._save_settings({"dictation": {"mode": "push_to_talk"}}),
            ),
            (
                "Запись: нажать для запуска и остановки",
                lambda: self._save_settings({"dictation": {"mode": "toggle"}}),
            ),
            (
                "Запись: остановить по тишине",
                lambda: self._save_settings({"dictation": {"mode": "auto_stop"}}),
            ),
            (
                "Запись: только вручную",
                lambda: self._save_settings({"dictation": {"mode": "manual"}}),
            ),
            (
                "Переключить безопасный ввод терминала",
                lambda: self._save_settings(
                    {
                        "dictation": {
                            "safe_terminal_input": not self.runtime.settings.dictation.safe_terminal_input
                        },
                    }
                ),
            ),
            ("Повторить последнюю вставку", self._insert_dashboard_result),
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
            from voxtype.ui.professional import ProfessionalWindow

            self._professional_window = ProfessionalWindow(self.controller, self.main)
            self._professional_window.insertion_requested.connect(self._submit_professional_text)
            self._professional_window.profiles_refreshed.connect(self._refresh_header_profile)
            self._professional_window.editor_draft_requested.connect(self._save_editor_draft)
            self._professional_window.scratchpad_toggle_requested.connect(self._toggle_scratchpad)
            self._professional_window.voice_edit_toggle_requested.connect(
                lambda: self._toggle_scratchpad(voice_edit=True)
            )
            self._start_professional_queue()
            if self._file_queue is not None:
                self._professional_window.bind_transcription_queue(self._file_queue)
        if not self._professional_window.open_section(page):
            self.main.shell.unavailable(
                page, "Раздел недоступен", "Нужный компонент отсутствует или отключён политикой."
            )
            return
        action = self._professional_window.section_action(page)
        if action is not None:
            self.main.shell.set_context_action(page, *action)
        self._embed_page(page, self._professional_window)
        if self._professional_window.transcription_panel is not None:
            self._professional_window.transcription_panel.refresh()

    def _open_recognition_editor(self) -> None:
        snapshot = self.controller.snapshot()
        if self._closing or not snapshot.full_transcript or snapshot.recognition_result is None:
            return
        edited = self.main.result_text()
        self.show_professional_tools()
        if self._professional_window is not None:
            self._professional_window.open_recognition_editor(
                snapshot.recognition_result,
                edited_text=edited if edited.strip() else None,
            )

    def _open_scratchpad(self) -> None:
        self.show_professional_tools()
        if self._professional_window is not None:
            self._professional_window.open_scratchpad()

    def _toggle_scratchpad(self, *, voice_edit: bool = False) -> None:
        window = self._professional_window
        if window is None or self._closing:
            return
        if voice_edit and not self.controller.policy.allow_voice_commands:
            return
        if self._scratchpad_session is not None:
            self.controller.stop_dictation()
        elif self.controller.start_dictation(local_only=True):
            readiness = self.controller.snapshot().readiness
            self._scratchpad_session = readiness.session_id if readiness is not None else None
            self._scratchpad_voice_edit = voice_edit
            if voice_edit:
                window.prepare_voice_edit()
            window.set_scratchpad_recording(True, voice_edit=voice_edit)
        else:
            readiness = self.controller.snapshot().readiness
            self._notify_desktop(
                "scratchpad-start",
                readiness.message if readiness is not None else "Диктовка недоступна",
                "warning",
            )

    def _observe_scratchpad(self, snapshot: object) -> None:
        identifier = getattr(self, "_scratchpad_session", None)
        if identifier is None:
            return
        result = getattr(snapshot, "recognition_result", None)
        state = getattr(snapshot, "state", "")
        window = self._professional_window
        if result is not None and result.session_id == identifier:
            voice_edit = self._scratchpad_voice_edit
            self._scratchpad_voice_edit = False
            self._scratchpad_session = None
            if window is not None:
                window.set_scratchpad_recording(False)
                if voice_edit:
                    if result.status is RecognitionStatus.COMPLETED:
                        window.apply_voice_edit(result)
                else:
                    window.append_scratchpad_result(result)
        elif state in {"COMPLETED", "ERROR_RECOVERABLE", "ERROR_FATAL"} and not (
            self._system_locked or self._system_sleeping
        ):
            self._scratchpad_session = None
            self._scratchpad_voice_edit = False
            if window is not None:
                window.set_scratchpad_recording(False)

    def _save_editor_draft(
        self, draft: tuple[str, str, str, tuple[TranscriptRevision, ...]]
    ) -> None:
        identifier, language, text, revisions = draft
        touched = time.time()

        def save() -> None:
            if (
                self.runtime.settings.privacy.recover_unsaved_drafts
                and not self.controller.policy.disable_history
            ):
                if len(text) > 250_000:
                    self.signals.data_status.emit(
                        "Черновик слишком большой для восстановления. Текст остаётся в редакторе.",
                        False,
                    )
                    return
                allowed_revisions = tuple(
                    version
                    for version in revisions
                    if len(version.text) <= 250_000
                    and (
                        version.source is not RevisionSource.RAW_ASR
                        or self.runtime.settings.privacy.save_history
                    )
                )
                self.runtime.session_repository.save_editor_draft(
                    identifier, language, text, touched, allowed_revisions
                )

        self._run_background("voxtype-save-editor-draft", save, finish_on_close=True)

    def _refresh_header_profile(self) -> None:
        self._header_profile_id = None
        self._update_views_if_changed(self.controller.snapshot())

    def _start_professional_queue(self) -> None:
        if self._closing or self._file_queue is not None or self._file_queue_starting:
            return
        from importlib.util import find_spec

        if find_spec("av") is None:
            return
        from voxtype.asr.transcription_queue import FileTranscriptionQueue

        self._file_queue_starting = True

        def load_queue() -> None:
            try:
                queue = FileTranscriptionQueue(
                    self.runtime.session_repository.path,
                    self.controller,
                    changed=self.signals.file_queue_changed.emit,
                )
            except (OSError, RuntimeError, sqlite3.Error):
                _LOGGER.exception("file_queue_initialization_failed")
                queue = None
            self.signals.file_queue_ready.emit(queue)

        if not self._run_background("voxtype-file-queue-load", load_queue):
            self._file_queue_starting = False

    def _on_file_queue_ready(self, queue: Any) -> None:
        self._file_queue_starting = False
        if self._closing or queue is None:
            return
        self._file_queue = queue
        if self._professional_window is not None:
            self._professional_window.bind_transcription_queue(queue)
            if self.main.shell.current == "files":
                self.show_professional_tools("files")
        self._poll_professional()

    def _poll_professional(self) -> None:
        queue = self._file_queue
        if self._closing or queue is None:
            return
        pending = self._pending_dictation_target
        if pending is not None and not queue.busy and not self._settings_write_running:
            self._pending_dictation_target = None
            foreground = capture_foreground()
            if foreground is not None and foreground.identity == pending.identity:
                self._invoke_ui("queued.start_dictation", self.controller.start_dictation)
            else:
                self._notify_desktop(
                    "dictation-waiting",
                    "Активное окно изменилось. Запустите диктовку снова",
                    "warning",
                )
        try:
            if (
                self._pending_dictation_target is None
                and not self._settings_write_running
                and not self._interactive_asr.is_set()
            ):
                queue.tick()
            if self._professional_window is not None:
                panel = self._professional_window.transcription_panel
                if panel is not None and panel.isVisible():
                    panel.refresh()
        except (RuntimeError, OSError) as exc:
            _LOGGER.warning("professional_queue_update_failed type=%s", type(exc).__name__)
        if queue.needs_tick or self._pending_dictation_target is not None:
            if not self._professional_timer.isActive():
                self._professional_timer.start()
        else:
            self._professional_timer.stop()

    def _notify_desktop(self, key: str, text: str, kind: str = "info") -> None:
        if self.main.isVisible():
            self.main.shell.notifications.notify(key, text, kind)
        else:
            self.tray.show_message("VoxType", text, critical=kind == "error")

    def _submit_professional_text(
        self,
        text: str,
        session: int | None = None,
        *,
        multiline_confirmed: bool = False,
        delivery_id: str | None = None,
    ) -> None:
        if not text:
            return
        if session is None and not multiline_confirmed:
            if not self._confirm_multiline_delivery(text):
                return
            multiline_confirmed = True

        def deliver() -> None:
            try:
                if not self.controller.insert_professional_text(
                    text,
                    session,
                    multiline_confirmed=multiline_confirmed,
                    delivery_id=delivery_id,
                ):
                    self.signals.professional_delivery_failed.emit()
            finally:
                if session is None:
                    self.signals.dashboard_delivery_finished.emit()

        def start() -> None:
            if (
                not self._run_background("voxtype-professional-insertion", deliver)
                and session is None
            ):
                self._finish_dashboard_delivery()

        if session is None:
            self._schedule_manual_delivery(start)
        else:
            start()

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
            filename, _ = QFileDialog.getSaveFileName(
                dialog, "Экспорт черновика", "VoxType.txt", "TXT (*.txt)"
            )
            if filename:
                try:
                    Path(filename).write_text(preview.toPlainText(), encoding="utf-8")
                except OSError:
                    self.tray.show_message(
                        "VoxType", "Не удалось сохранить черновик", critical=True
                    )

        export_button.clicked.connect(save)
        dialog.open()

    def _on_professional_action(
        self,
        session: int,
        action: VoiceAction,
        profile: DictationProfile,
        resolution: CommandResolution | None = None,
    ) -> None:
        if (
            self._closing
            or not self.controller.policy.allow_voice_commands
            or not self.controller.professional_session_is_current(session)
        ):
            return
        if resolution is not None and not self.controller.claim_live_command(session, resolution):
            return
        from PySide6.QtWidgets import QMessageBox

        if (
            action.confirmation_required
            and QMessageBox.question(
                self.main, "Голосовая команда", f"Выполнить команду «{action.trigger}»?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if not self.controller.professional_session_is_current(session):
            return
        kind, payload = action.action_type, action.payload
        if kind == "CancelCurrentSession":
            self.controller.cancel_professional_session(session)
        elif kind == "OpenVoxTypePanel":
            if payload.get("panel") == "settings":
                self.show_settings()
            elif payload.get("panel") == "transcript_editor":
                self._open_scratchpad()
            elif payload.get("panel") in {"dictionary", "snippets", "commands", "replacements"}:
                self.show_professional_tools(
                    payload["panel"]
                    if payload["panel"] in {"dictionary", "snippets"}
                    else "profiles"
                )
                if self._professional_window is not None:
                    self._professional_window.open_collection(payload["panel"])
        elif kind == "CopyLastResult":
            self._run_background(
                "voxtype-professional-copy", lambda: self.controller.copy_previous_result(session)
            )
        elif kind == "SwitchProfile":
            name = payload.get("name", "").casefold()
            profiles = self.controller.profiles
            selected = next(
                (
                    item
                    for item in profiles.profiles()
                    if self.controller.profile_allowed(item)
                    and name in {item.id.casefold(), item.name.casefold()}
                ),
                None,
            )
            if selected is not None:

                def switch_profile() -> None:
                    if self.controller.professional_session_is_current(session):
                        profiles.set_default(selected.id)
                        self.signals.state_changed.emit()

                self._run_background("voxtype-switch-profile", switch_profile)
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
            text = {"InsertParagraph": "\n\n", "InsertNewline": "\n"}.get(
                kind, payload.get("text", "")
            )
            self._submit_professional_text(text, session)

    def show_settings(self, page: str = "settings") -> None:
        if not self.main.isVisible():
            self._show_main()
        first = self._settings_window is None
        from_models = self.main.shell.current == "models"
        window = self.settings_window
        if page == "models":
            window.open_models()
        elif from_models:
            window.open_general()
        self._embed_page(page, window)
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

    def _set_model_choices(
        self, installed: dict[str, int], *, preserve_selection: bool = False
    ) -> None:
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
        self._installed_model_sizes = installed
        self._refresh_model_cards(self.controller.snapshot().readiness.phase.value)
        window.gpu_experimental_check.setChecked(False)
        window.gpu_experimental_check.setEnabled(False)

    def _refresh_model_cards(self, phase: str) -> None:
        window = self._settings_window
        if window is None:
            return
        installed = self._installed_model_sizes
        descriptions = {
            "small": "Модель для повседневной диктовки и работы на CPU.",
            "large-v3-turbo": "Режим максимальной точности для сложных текстов.",
        }
        policy = self.controller.policy
        integrity_labels = {
            ModelIntegrityState.INCOMPLETE: "Комплект файлов неполный",
            ModelIntegrityState.CORRUPTED: "Файлы модели повреждены",
            ModelIntegrityState.INCOMPATIBLE: "Формат модели несовместим",
        }
        cards = []
        for identifier in descriptions:
            size = installed.get(identifier)
            usable = policy.model_allowed(identifier)
            active = identifier == self.controller.backend.model_id
            state = (
                "Загрузка файлов"
                if identifier == self._downloading_model
                else "Ошибка последней установки"
                if identifier == self._failed_model
                else "Загрузка в память"
                if active and phase == "model_loading"
                else "Готова к работе"
                if active and self.controller.active_model_loaded
                else "Выбрана · загрузится при запуске диктовки"
                if active and identifier in installed
                else "Установлена"
                if identifier in installed
                else integrity_labels.get(self._model_integrity.get(identifier), "Не установлена")
            )
            cards.append(
                (
                    identifier,
                    descriptions[identifier],
                    f"{size / 1024**2:.0f} МиБ" if size is not None else "Размер не указан",
                    identifier in installed,
                    active,
                    usable and not policy.enforce_offline_only,
                    usable,
                    state,
                )
            )
        window.set_model_cards(cards)
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
        manager = self.runtime.model_manager
        identifiers = {spec.model_id for spec in DEFAULT_MODEL_SPECS.values()}
        self.signals.models_ready.emit(
            (
                {item.model_id: item.size_bytes for item in manager.list_installed()},
                {identifier: manager.integrity_state(identifier) for identifier in identifiers},
            )
        )

    def _on_devices_ready(self, items: object) -> None:
        if not isinstance(items, (list, tuple)):
            return
        self.runtime.devices = tuple(items)
        audio = self.runtime.settings.audio
        selected = match_input_device(
            self.runtime.devices, audio.device_id, audio.device_fingerprint
        )
        self.controller.set_recording_availability(microphone=selected is not None)
        devices = [(f"{item.name} ({item.host_api})", item.index) for item in items]
        if self._settings_window is not None:
            unavailable_id = (
                -1 if audio.device_fingerprint and selected is None else audio.device_id
            )
            self._settings_window.set_audio_devices(
                devices,
                selected.index
                if selected is not None and audio.device_id is not None
                else unavailable_id,
            )
        if self.onboarding is not None:
            selected = self.onboarding.microphone_combo.currentData()
            self.onboarding.set_microphones(devices, selected)

    def _on_models_ready(
        self, inventory: tuple[dict[str, int], dict[str, ModelIntegrityState]]
    ) -> None:
        installed, self._model_integrity = inventory
        self._set_model_choices(installed, preserve_selection=True)

    def _save_settings(self, values: object) -> None:
        if self._closing or not isinstance(values, dict):
            return
        values = dict(values)
        if self._settings_write_running:
            for section, changes in values.items():
                pending = self._deferred_settings.get(section)
                self._deferred_settings[section] = (
                    {**pending, **changes}
                    if isinstance(pending, dict) and isinstance(changes, dict)
                    else changes
                )
            return
        if not self.controller.can_change_runtime_settings():
            for section in ("audio", "dictation", "model", "hotkeys"):
                if section in values:
                    self._deferred_settings[section] = values.pop(section)
            if self._deferred_settings:
                self._notify_desktop(
                    "settings-pending", "Параметры записи применятся после завершения операции"
                )
        audio = values.get("audio")
        if isinstance(audio, dict) and "device_id" in audio:
            audio = dict(audio)
            index = audio["device_id"]
            selected = next((item for item in self.runtime.devices if item.index == index), None)
            previous_audio = self.runtime.settings.audio
            if index is None:
                audio["device_fingerprint"] = ""
            elif selected is not None and (
                index != previous_audio.device_id
                or not previous_audio.device_fingerprint
                or selected.fingerprint == previous_audio.device_fingerprint
            ):
                audio["device_fingerprint"] = selected.fingerprint
            values["audio"] = audio
        policy = self.controller.policy
        if isinstance(values.get("privacy"), dict):
            privacy = dict(values["privacy"])
            if policy.disable_history:
                privacy.pop("save_history", None)
                privacy.pop("recover_unsaved_drafts", None)
            if policy.disable_audio_storage:
                privacy.pop("save_audio", None)
            values["privacy"] = privacy
        previous = self.runtime.settings
        model_values = values.get("model")
        dictation_values = values.get("dictation")
        requested_model = previous.model.installed_model
        requested_profile = previous.dictation.model_profile.value
        if isinstance(model_values, dict):
            model_values = dict(model_values)
            requested_model = model_values.pop("installed_model", requested_model)
            values["model"] = model_values
        if isinstance(dictation_values, dict):
            dictation_values = dict(dictation_values)
            requested_profile = dictation_values.pop("model_profile", requested_profile)
            values["dictation"] = dictation_values
        if (
            requested_profile == previous.dictation.model_profile.value
            and requested_model != previous.model.installed_model
        ):
            requested_profile = requested_model
        selected = _coerce_asr_profile(str(requested_profile))
        hotkey_values = values.get("hotkeys")
        try:
            candidate = (
                replace(previous.hotkeys, **hotkey_values)
                if isinstance(hotkey_values, dict)
                else previous.hotkeys
            )
            mode = (
                DictationMode(dictation_values.get("mode", previous.dictation.mode))
                if isinstance(dictation_values, dict)
                else previous.dictation.mode
            )
            changed_hotkeys = candidate != previous.hotkeys or mode != previous.dictation.mode
            if changed_hotkeys and not self._configure_hotkeys(settings=candidate, mode=mode):
                self.settings_window.set_values(previous)
                return
        except (TypeError, ValueError):
            self._notify_desktop("hotkey-conflict", failure_message("hotkey_conflict"), "warning")
            return
        if not values:
            return
        self._settings_write_running = True

        def persist() -> None:
            try:
                dictation = values.get("dictation")
                if isinstance(dictation, dict) and "custom_vocabulary" in dictation:
                    dictation["custom_vocabulary"] = list(
                        Vocabulary(dictation["custom_vocabulary"]).entries
                    )
                updated = self.runtime.settings_repository.update(values)
            except Exception as exc:
                _LOGGER.warning("settings_save_failed kind=%s", type(exc).__name__)
                updated = None
            self.signals.settings_saved.emit(
                lambda: self._apply_saved_settings(
                    updated, previous, selected, requested_model, changed_hotkeys
                )
            )

        self._settings_writer = threading.Thread(
            target=persist,
            name="voxtype-settings-writer",
            daemon=True,
        )
        self._settings_writer.start()

    def _finish_settings_write(self, apply: Any) -> None:
        self._settings_write_running = False
        if not self._closing:
            apply()
            self.signals.state_changed.emit()

    def _apply_saved_settings(
        self,
        updated: AppSettings | None,
        previous: AppSettings,
        selected: ASRProfile,
        requested_model: object,
        changed_hotkeys: bool,
    ) -> None:
        if updated is None:
            if changed_hotkeys:
                self._configure_hotkeys(settings=previous.hotkeys)
            if self._settings_window is not None:
                self._settings_window.set_values(previous)
            self._notify_desktop("settings-save", failure_message("configuration_error"), "error")
            return
        vocabulary = Vocabulary(updated.dictation.custom_vocabulary)
        theme_changed = updated.general.theme != previous.general.theme
        language_changed = updated.general.ui_language != previous.general.ui_language
        audio_changed = updated.audio != previous.audio
        vad_changed = updated.audio.vad_sensitivity != previous.audio.vad_sensitivity
        self.runtime.settings = updated
        self.controller.settings = updated
        self.controller.set_language(updated.dictation.language, persist=False)
        self.controller.vocabulary = vocabulary
        self.controller.target_manager.set_policy(TargetPolicy(updated.dictation.target_mode.value))
        self.controller.injection_service.strategy = updated.dictation.injection_mode.value
        self.controller.injection_service.paste_delay = (
            max(350, updated.dictation.insertion_delay_ms) / 1_000.0
        )
        self.main.set_minimize_to_tray(updated.general.minimize_to_tray)
        if updated.model.power_mode != previous.model.power_mode:
            self._apply_motion_profile()
        self._update_idle_model_timer(self.controller.resource_phase)
        if language_changed:
            self.main.set_interface_language(updated.general.ui_language)
        if updated.dictation.mode != previous.dictation.mode or updated.hotkeys != previous.hotkeys:
            self._update_mode_indicator()
        if theme_changed:
            apply_theme(self.application, ThemeMode(updated.general.theme))
        if updated.general.start_with_windows != previous.general.start_with_windows:
            self.runtime.startup.set_enabled(updated.general.start_with_windows)
        if audio_changed:
            self._configure_capture_factory()
        if vad_changed:
            self._configure_segmenter_factory()
        backend_settings_changed = (
            requested_model != previous.model.installed_model
            or updated.model.cpu_threads != previous.model.cpu_threads
            or updated.model.power_mode != previous.model.power_mode
            or updated.model.decoding_preset != previous.model.decoding_preset
            or domain_profile(selected) != previous.dictation.model_profile
        )
        required_model = DEFAULT_MODEL_SPECS[selected].model_id
        if backend_settings_changed:

            def activate() -> None:
                if not self.runtime.model_manager.is_installed(required_model):
                    self.signals.settings_status.emit(
                        f"Для выбранного профиля сначала скачайте модель {required_model}",
                        False,
                    )
                    return
                activated = self.controller.configure_backend(
                    self.runtime.backend_for(selected),
                    domain_profile(selected),
                    self.runtime.retry_backend_for(selected),
                )
                self.signals.settings_status.emit(
                    f"Активная модель: {required_model}"
                    if activated
                    else "Модель не активирована; предыдущая модель сохранена",
                    activated,
                )

            self._run_background("voxtype-activate-model", activate)

    def _configure_capture_factory(self) -> None:
        # Discovery runs in the settings worker. Saving settings must never
        # synchronously wait for PortAudio on the Qt thread.
        devices = self.runtime.devices
        selected_index = self.runtime.settings.audio.device_id
        fingerprint = self.runtime.settings.audio.device_fingerprint
        device = match_input_device(devices, selected_index, fingerprint)
        self.controller.capture_factory = self._capture_factory_for(selected_index, fingerprint)
        self.controller.set_recording_availability(microphone=device is not None)
        self.controller.microphone_name = (
            f"{device.name} ({device.host_api})" if device else "Микрофон недоступен"
        )
        if device is not None:
            self.runtime.device = device

    def _capture_factory_for(self, selected_index: int | None, fingerprint: str):
        profile = AudioPreprocessingProfile(self.runtime.settings.audio.preprocessing_profile.value)
        processing = select_preprocessing(
            self.runtime.settings.audio.preprocessing_mode,
            profile,
            normalize=self.runtime.settings.audio.normalization,
            noise_gate=self.runtime.settings.audio.noise_suppression,
        )

        def factory(queue: LosslessAudioQueue) -> AudioCaptureService:
            current = (
                list_input_devices(candidate_sample_rates=(16_000, 48_000))
                if selected_index is not None
                else ()
            )
            selected = resolve_recording_device(selected_index, current, fingerprint)
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

        return factory

    def _configure_segmenter_factory(self) -> None:
        vad_path = self.runtime.data_directory / "models" / "silero-vad" / "silero_vad.onnx"
        sensitivity = self.runtime.settings.audio.vad_sensitivity

        def factory() -> StreamingSegmenter:
            threshold = min(0.75, max(0.25, 0.70 - sensitivity * 0.5))
            settings = replace(SegmentationSettings(), speech_threshold=threshold)
            return StreamingSegmenter(SileroVAD(vad_path, strict=False), settings=settings)

        self.controller.segmenter_factory = factory

    def _check_updates(self) -> None:
        if self._update_busy or self._closing:
            return
        if self.controller.policy.enforce_offline_only:
            self._show_data_status("Обновления по сети отключены политикой.", False)
            return
        current = version_info(self.runtime.settings.general.release_channel)
        self._update_busy = True

        def check() -> None:
            try:
                client = UpdateMetadataClient.from_configuration(current)
                release = client.check()
            except UpdatesUnavailable:
                self.signals.update_checked.emit(None, "Доверенный канал обновлений не настроен.")
            except Exception as exc:
                _LOGGER.warning("update_check_failed kind=%s", type(exc).__name__)
                self.signals.update_checked.emit(
                    None, "Проверка обновления не завершилась или подпись не прошла проверку."
                )
            else:
                self.signals.update_checked.emit(client, release)

        if not self._run_background("voxtype-update-check", check):
            self._update_busy = False

    def _confirm_update_download(self, client, release) -> None:
        self._update_busy = False
        if self._closing:
            return
        if client is None:
            self._show_data_status(release, False)
            return
        if release is None:
            self._show_data_status("Подходящих обновлений в выбранном канале нет.", True)
            return
        if (
            QMessageBox.question(
                self.main,
                "Подписанное обновление VoxType",
                f"Загрузить проверенную версию {release.version} ({release.size_bytes / 1024**2:.0f} МиБ)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        cancel = threading.Event()
        self._update_busy = True
        self._update_cancel = cancel
        dialog = QMessageBox(self.main)
        dialog.setWindowTitle("Загрузка обновления")
        dialog.setText("Загружаю и проверяю подпись установщика.")
        dialog.setStandardButtons(QMessageBox.StandardButton.Cancel)
        dialog.rejected.connect(cancel.set)
        self._update_dialog = dialog
        dialog.show()

        def download() -> None:
            try:
                staged = client.download(release, self.runtime.data_directory / "updates", cancel)
            except Exception as exc:
                _LOGGER.warning("update_download_stopped kind=%s", type(exc).__name__)
                staged = None
            self.signals.update_staged.emit(staged)

        if not self._run_background("voxtype-update-download", download):
            self._confirm_update_install(None)

    def _confirm_update_install(self, staged: StagedUpdate | None) -> None:
        cancelled = self._update_cancel is not None and self._update_cancel.is_set()
        self._update_cancel = None
        self._update_busy = False
        if self._update_dialog is not None:
            self._update_dialog.hide()
            self._update_dialog.deleteLater()
            self._update_dialog = None
        if self._closing or cancelled:
            return
        if staged is None:
            self._show_data_status("Установщик не прошёл проверку или загрузка прервана.", False)
            return
        if not self.controller.can_change_runtime_settings() or self._settings_write_running:
            self._show_data_status(
                "Завершите текущие операции и снова проверьте обновления.", False
            )
            return
        if (
            QMessageBox.question(
                self.main,
                "Установить обновление",
                f"Завершить VoxType и открыть проверенный установщик {staged.info.version}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
            and self.controller.can_change_runtime_settings()
        ):
            self.installer_handoff = staged
            self.close()

    def _show_data_status(self, message: str, ok: bool) -> None:
        if not self._closing:
            self._notify_desktop("local-data", message, "success" if ok else "error")

    def _create_backup(self, *, automatic: bool = False) -> None:
        if self._backup_running:
            return
        self._backup_running = True

        def backup() -> None:
            try:
                manifest = self._backups.create(automatic=automatic)
            except (OSError, sqlite3.Error, ValueError) as exc:
                _LOGGER.warning("backup_failed kind=%s", type(exc).__name__)
                if not automatic:
                    self.signals.data_status.emit("Не удалось создать резервную копию.", False)
            else:
                if not automatic and manifest is not None:
                    self.signals.data_status.emit(
                        f"Резервная копия создана в {manifest.parent}", True
                    )
            finally:
                self._backup_running = False

        if not self._run_background("voxtype-backup", backup):
            self._backup_running = False

    def _choose_backup_restore(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self.settings_window,
            "Резервная копия VoxType",
            str(self._backups.root),
            "Манифест (manifest.json)",
        )
        if (
            not filename
            or QMessageBox.question(
                self.main,
                "Восстановление данных",
                "При следующем запуске выбранная база заменит текущую. Перед заменой VoxType сохранит резервную копию текущей базы.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def stage() -> None:
            try:
                self._backups.stage_restore(Path(filename))
            except (OSError, sqlite3.Error, ValueError) as exc:
                _LOGGER.warning("restore_stage_failed kind=%s", type(exc).__name__)
                self.signals.data_status.emit(
                    "Копию не удалось проверить или уже запланировано восстановление.", False
                )
            else:
                self.signals.data_status.emit(
                    "Копия проверена. Восстановление пройдёт при следующем запуске.", True
                )

        self._run_background("voxtype-stage-restore", stage)

    def _export_personal_data(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self.settings_window, "Экспорт настроек", "VoxType-settings.json", "JSON (*.json)"
        )
        if not filename:
            return

        def export() -> None:
            from voxtype.settings.personal_data import export_configuration

            try:
                export_configuration(self.runtime.settings_repository, Path(filename))
            except (OSError, ValueError, TypeError) as exc:
                _LOGGER.warning("personal_export_failed kind=%s", type(exc).__name__)
                self.signals.data_status.emit("Не удалось экспортировать настройки.", False)
            else:
                self.signals.data_status.emit("Настройки экспортированы.", True)

        self._run_background("voxtype-personal-export", export)

    def _export_personal_archive(self, category: str) -> None:
        if self.controller.policy.disable_history:
            self._show_data_status("Экспорт содержимого отключён политикой.", False)
            return
        title = "Архив диктовок" if category == "history" else "Архив транскриптов"
        filename, _ = QFileDialog.getSaveFileName(
            self.settings_window, title, f"VoxType-{category}.json", "JSON (*.json)"
        )
        if not filename:
            return

        def export() -> None:
            from voxtype.settings.personal_data import export_archive

            try:
                export_archive(self.runtime.session_repository.path, category, Path(filename))
            except (OSError, sqlite3.Error, ValueError):
                self.signals.data_status.emit("Не удалось экспортировать выбранный архив.", False)
            else:
                self.signals.data_status.emit("Архив экспортирован.", True)

        self._run_background("voxtype-personal-archive", export)

    def _import_personal_data(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self.settings_window, "Импорт настроек", "", "JSON (*.json)"
        )
        if not filename:
            return

        def stage() -> None:
            from voxtype.settings.personal_data import stage_configuration

            try:
                changes, summary = stage_configuration(
                    self.runtime.settings_repository, Path(filename)
                )
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                self.signals.data_status.emit("Формат пакета настроек не поддерживается.", False)
            else:
                self.signals.personal_import_preview.emit(Path(filename), changes, summary)

        self._run_background("voxtype-personal-import-preview", stage)

    def _confirm_personal_import(self, path: Path, changes: dict, summary: tuple[str, ...]) -> None:
        if self._closing or self._settings_write_running:
            return
        lines = "\n".join(
            (
                *summary,
                "Заменить тему, язык интерфейса и горячие клавиши.",
                "Настройки вступят в силу после перезапуска.",
            )
        )
        if (
            QMessageBox.question(
                self.main,
                "Подтвердить импорт",
                lines,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def apply() -> None:
            from voxtype.settings.personal_data import stage_configuration

            repository = self.runtime.settings_repository
            try:
                with repository._lock:
                    latest, _ = stage_configuration(repository, path)
                    if latest != changes:
                        raise ValueError("The preview is stale")
                    repository.update(latest)
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                self.signals.data_status.emit(
                    "Пакет или локальные настройки изменились. Откройте импорт снова.", False
                )
            else:
                self.signals.data_status.emit(
                    "Настройки импортированы. Перезапустите VoxType.", True
                )

        self._run_background("voxtype-personal-import-apply", apply)

    def _check_recovery(self) -> None:
        if self.runtime.session_repository.recoverable(
            include_drafts=self.runtime.settings.privacy.recover_unsaved_drafts,
            limit=1,
        ):
            self.signals.recovery_available.emit()

    def show_recovery_center(self) -> None:
        self.show_history(recovery=True)

    def show_history(self, *, recovery: bool = False) -> None:
        self._history_recovery = recovery
        if self.controller.policy.disable_history:
            self.main.shell.unavailable(
                "history", "История отключена", "Организация запретила сохранение истории диктовки."
            )
            return
        if not self.main.isVisible():
            self._show_main()
        self.main.shell.set_context_action("history", "Обновить", self._refresh_history)
        self._embed_page("history", self.history_window)
        self._refresh_history()

    def _load_delivery_outbox(self) -> None:
        if self.controller.policy.disable_history or not self.runtime.settings.privacy.save_history:
            self._notify_desktop(
                "outbox-off",
                "Постоянное сохранение выключено. Текущий результат доступен до выхода из VoxType.",
            )
            return

        def load() -> None:
            try:
                entries = self.runtime.session_repository.pending_deliveries()
            except (OSError, sqlite3.Error):
                self.signals.data_status.emit("Не удалось загрузить список вставок.", False)
            else:
                self.signals.delivery_outbox_ready.emit(entries)

        self._run_background("voxtype-delivery-outbox", load)

    def _show_delivery_outbox(self, entries: tuple) -> None:
        if (
            self._closing
            or self.controller.policy.disable_history
            or not self.runtime.settings.privacy.save_history
        ):
            return
        if not entries:
            self._notify_desktop("outbox-empty", "Нет результатов, ожидающих проверки доставки.")
            return
        states = {
            "pending": "Ожидает отправки",
            "attempting": "Исход предыдущей попытки неизвестен",
            "submitted_unconfirmed": "Команда отправлена; проверьте целевое поле",
            "failed": "Вставка не завершена",
        }
        options = [
            f"{index}. {time.strftime('%d.%m.%Y %H:%M', time.localtime(item.created_at))} · "
            f"{item.target_executable or 'Цель не задана'} · {states[item.status]} · "
            + " ".join(item.text[:100].split())
            for index, item in enumerate(entries, 1)
        ]
        selected, ok = QInputDialog.getItem(
            self.history_window,
            "Результаты вставки",
            "Выберите результат",
            options,
            editable=False,
        )
        if not ok:
            return
        item = entries[options.index(selected)]
        dialog = QMessageBox(self.history_window)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setWindowTitle("Результат вставки")
        dialog.setText(states[item.status])
        copied = dialog.addButton("Скопировать", QMessageBox.ButtonRole.ActionRole)
        retry = dialog.addButton(
            "Выбрать поле и вставить заново", QMessageBox.ButtonRole.ActionRole
        )
        retry.setEnabled(item.status != "attempting")
        confirmed = dialog.addButton("Подтвердить доставку", QMessageBox.ButtonRole.ActionRole)
        confirmed.setEnabled(item.status != "pending")
        removed = dialog.addButton("Убрать из списка", QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        button = dialog.clickedButton()
        if button is copied:
            self.application.clipboard().setText(item.text)
        elif button is retry:
            self._submit_professional_text(item.text, delivery_id=item.delivery_id)
        elif button is confirmed or button is removed:
            acknowledged = button is confirmed

            def update() -> None:
                repository = self.runtime.session_repository
                if acknowledged:
                    repository.finish_delivery(item.delivery_id, "confirmed")
                else:
                    repository.delete_delivery(item.delivery_id)

            self._run_background("voxtype-update-outbox", update)

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
        recovery = self._history_recovery

        def read_history() -> None:
            try:
                repository = self.runtime.session_repository
                if recovery:
                    candidates = repository.recoverable(
                        include_drafts=self.runtime.settings.privacy.recover_unsaved_drafts,
                    )
                    sessions = tuple(
                        record
                        for item in candidates
                        if (record := repository.get(item.session_id)) is not None
                    )
                    more = False
                else:
                    sessions, more = repository.search(**options)
            except Exception:
                _LOGGER.exception("history_load_failed")
                self.signals.history_ready.emit(revision, None)
            else:
                self.signals.history_ready.emit(revision, (sessions, more))

        if not self._run_background("voxtype-history-load", read_history):
            self._history_loading = False
            self.history_window.set_loading(False)

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

    def _open_restored_draft(
        self, identifier: str, text: str, versions: tuple[TranscriptRevision, ...]
    ) -> None:
        if self._closing or not text:
            return
        self.show_professional_tools()
        if self._professional_window is not None:
            self._professional_window.open_restored_draft(identifier, text, versions)

    def _recover_history_session(self, session_id: str) -> None:
        def worker() -> None:
            try:
                source = self.runtime.session_repository.get(session_id)
                text = self.controller.recover_session(session_id)
                if source is not None and source.source_type == "draft":
                    versions = self.runtime.session_repository.editor_revisions(session_id)
                    self.signals.draft_restored.emit(session_id, text, versions)
                else:
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
        selected, _filter = QFileDialog.getSaveFileName(
            self.history_window,
            "Экспорт текста",
            f"VoxType-{session_id}.txt",
            "Text files (*.txt)",
        )
        if not selected:
            return

        def export() -> None:
            try:
                record = self.runtime.session_repository.get(session_id)
                if record is not None:
                    Path(selected).write_text(record.committed_text, encoding="utf-8")
            except (OSError, sqlite3.Error):
                self.signals.settings_status.emit("Не удалось сохранить запись истории", False)

        self._run_background("voxtype-history-export", export)

    def show_diagnostics(self) -> None:
        self.diagnostics_window.show()
        self.diagnostics_window.raise_()
        self._refresh_diagnostics()

    def _show_live_resource_counters(self) -> None:
        if self._closing or self._diagnostics_window is None:
            return
        counters = self.controller.resource_counters()
        metrics = self.controller.metrics.snapshot()
        timers = sum(
            timer.isActive()
            for timer in (
                self.timer,
                self._idle_model_timer,
                self._professional_timer,
                self._practice_timer,
                self._heartbeat_timer,
                self._recovery_timer,
            )
        )
        model = "в памяти" if counters["resident_model"] else "выгружена"
        self._diagnostics_window.show_resource_counters(
            f"Рабочие потоки {counters['workers']} · ASR {counters['asr_active']}/1 · "
            f"Буфер PCM {counters['audio_chunks']} блоков, {counters['audio_seconds']:.1f} с · "
            f"Потери {counters['dropped_frames']} сэмплов, переполнения {counters['overflows']} · "
            f"Таймеры контроллера {timers} · Модель {model} · "
            f"Средняя задержка ASR {metrics['latency_average_s']:.2f} с"
        )

    def _refresh_diagnostics(self) -> None:
        self.diagnostics_window.set_self_test_running(True, "Выполняю локальные проверки…")

        def worker() -> None:
            try:
                hardware = probe_hardware()
                self.controller.sample_resources()
                data = self.runtime.data_directory
                checks = run_health_checks(data)
                categories = {
                    "Модели": (data / "models",),
                    "История, очередь файлов": tuple(
                        data / name
                        for name in (
                            "sessions.sqlite3",
                            "sessions.sqlite3-wal",
                            "sessions.sqlite3-shm",
                        )
                    ),
                    "Восстановление и копии": (
                        data / "backups",
                        cache_directory_path() / "audio-spool",
                    ),
                    "Записанное аудио": (data / "audio",),
                    "Диагностический журнал": (self.runtime.log_path,),
                    "Подготовка обновлений": (data / "updates",),
                }
                storage = {}
                for label, paths in categories.items():
                    size, complete = measure_owned_paths(paths)
                    storage[f"Диск · {label}"] = (
                        "≥" if not complete else ""
                    ) + f"{size / 1024**2:.1f} МиБ"
                version = version_info(self.runtime.settings.general.release_channel)
                libraries: dict[str, str] = {
                    "VoxType": version.app_version,
                    "Сборка": version.build_id,
                    "Канал обновлений": version.release_channel.value,
                }
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
                    }
                    | storage,
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
        settings = self.runtime.settings
        wizard.restore_choices(
            settings.dictation.language.value,
            settings.hotkeys.toggle,
            settings.hotkeys.language,
            settings.hotkeys.pause,
        )
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
        wizard.rejected.connect(self._stop_practice)
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
        if self._model_download_cancel is not None:
            if self._model_download_dialog is not None:
                self._model_download_dialog.show()
            return
        selected = _coerce_asr_profile(profile_text)
        spec = DEFAULT_MODEL_SPECS[selected]
        policy = self.controller.policy
        if not policy.model_allowed(spec.model_id):
            self.signals.model_ready.emit(
                False, "Загрузка модели запрещена политикой организации", spec.model_id
            )
            return
        cancel = self._model_download_cancel = threading.Event()
        self._downloading_model = spec.model_id
        self._failed_model = None
        self._refresh_model_cards(self.controller.snapshot().readiness.phase.value)
        if self.main.shell.current in {"settings", "models"} and self._settings_window is not None:
            if self._model_download_dialog is not None:
                self._model_download_dialog.show()
                self._model_download_dialog.raise_()
                return
            self._model_download_dialog = ModelDownloadDialog(spec.model_id, self.main, cancel.set)
            self._model_download_dialog.show()

        def worker() -> None:
            try:
                last_progress = -1.0

                def progress(done: int, total: int | None) -> None:
                    nonlocal last_progress
                    now = time.monotonic()
                    if now - last_progress < 0.125 and (total is None or done < total):
                        return
                    last_progress = now
                    percent = int(done * 100 / total) if total else 0
                    self.signals.model_progress.emit(percent, f"{done / 1024**2:.0f} МБ")

                installed = self.runtime.model_manager.is_installed(spec.model_id)
                if not installed:
                    if policy.enforce_offline_only:
                        self.signals.model_ready.emit(
                            False,
                            "Политика разрешает только уже установленные модели",
                            spec.model_id,
                        )
                        return
                    self.runtime.model_manager.install(spec, progress=progress, cancel=cancel)
                if cancel.is_set() or self._closing:
                    raise ModelInstallCancelled("Загрузка отменена")
                install_silero_vad(
                    self.runtime.data_directory,
                    allow_download=not installed and not policy.enforce_offline_only,
                )
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
            except ModelInstallCancelled:
                self.signals.model_ready.emit(False, "Загрузка модели отменена", spec.model_id)
            except Exception as exc:
                _LOGGER.exception("model_install_failed type=%s", type(exc).__name__)
                self.signals.model_ready.emit(
                    False, failure_message("model_download_failed"), spec.model_id
                )

        if not self._run_background("voxtype-model-install", worker):
            self._on_model_ready(False, "Дождитесь завершения фоновых операций", spec.model_id)

    def _on_model_progress(self, percent: int, message: str) -> None:
        if self._closing:
            return
        if self._model_download_dialog is not None:
            self._model_download_dialog.update_progress(percent, message)
        if self._settings_window is not None:
            self._settings_window.set_model_download_progress(percent, message)
        if self.onboarding is not None:
            self.onboarding.set_model_progress(percent, message)

    def _on_model_ready(self, ready: bool, message: str, model_id: str) -> None:
        self._model_download_cancel = None
        self._downloading_model = None
        self._failed_model = None if ready else model_id
        if self._closing:
            return
        self._refresh_model_cards(self.controller.snapshot().readiness.phase.value)
        if self._model_download_dialog is not None:
            self._model_download_dialog.close()
            self._model_download_dialog.deleteLater()
            self._model_download_dialog = None
        if self._settings_window is not None:
            self._settings_window.set_model_download_progress(100)
            self._settings_window.set_diagnostics_status(message, ready)
        if ready and self._settings_window is not None:
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
            self.settings_window.set_diagnostics_status(
                "Модель запрещена политикой организации", False
            )
            return
        if self._interactive_asr.is_set() or self.controller.reserve_file_recognition() is None:
            self.settings_window.set_diagnostics_status(
                "Дождитесь завершения текущего распознавания", False
            )
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
                quality = "есть запас" if rtf <= 0.7 else "рекомендуется профиль «Быстро»"
                message = f"RTF={rtf:.3f}; {quality}"
            except Exception as exc:
                _LOGGER.exception("model_benchmark_failed type=%s", type(exc).__name__)
                ok, message = False, failure_message("benchmark")
            finally:
                self.controller.release_file_recognition()
            try:
                if ok:
                    ok = self.controller.configure_backend(
                        backend,
                        domain_profile(selected),
                        self.runtime.retry_backend_for(selected),
                    )
                    if not ok:
                        message = "Замер завершён. Настройки модели применить не удалось."
            finally:
                self._interactive_asr.clear()
            self.signals.settings_status.emit(message, ok)
            self.signals.state_changed.emit()

        threading.Thread(target=worker, name="voxtype-model-benchmark", daemon=True).start()

    def _stop_audio_test(self) -> None:
        if self._audio_test_cancel is not None:
            self._audio_test_cancel.set()

    def _audio_test(self, device_text: str) -> None:
        if self._closing or not self.controller.reserve_audio_monitor():
            self.settings_window.update_microphone_level(
                0.0, 0.0, "Завершите текущую операцию с микрофоном."
            )
            return
        cancel = self._audio_test_cancel = threading.Event()
        self.signals.state_changed.emit()

        def worker() -> None:
            try:
                selected = resolve_recording_device(
                    int(device_text) if device_text else None, self.runtime.devices
                )

                def display(observation) -> None:
                    if not cancel.is_set():
                        clipping = observation.clipping_ratio * 100.0
                        message = (
                            "Уменьшите усиление микрофона."
                            if clipping > 0.1
                            else "Проверка микрофона завершена."
                        )
                        self.signals.audio_level.emit(observation.rms, clipping, message)

                record_input(
                    selected,
                    2.0,
                    choose_capture_rate(selected),
                    cancel=cancel,
                    observe_levels=display,
                )
            except InterruptedError:
                pass
            except Exception as exc:
                _LOGGER.warning("microphone_test_failed type=%s", type(exc).__name__)
                if not cancel.is_set():
                    self.signals.audio_level.emit(0.0, 0.0, failure_message("microphone_test"))
            finally:
                self.controller.release_audio_monitor()
                self.signals.state_changed.emit()

        if not self._run_background("voxtype-audio-test", worker, finish_on_close=True):
            self.controller.release_audio_monitor()

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
        wizard = self.onboarding
        if wizard is None or self._closing:
            return
        if self._practice is not None:
            self._stop_practice()
            return
        device_id = wizard.microphone_combo.currentData()
        device = match_input_device(self.runtime.devices, device_id, "")
        if device is None:
            self.signals.phrase_ready.emit(language, failure_message("microphone_missing"), False)
            return
        if not self.controller.start_dictation(
            practice_language=Language(language),
            practice_capture=self._capture_factory_for(device_id, device.fingerprint),
        ):
            self.signals.phrase_ready.emit(
                language, self.controller.snapshot().readiness.message, False
            )
            return
        identifier = self.controller.snapshot().readiness.session_id
        if identifier is not None:
            self._practice = (language, identifier)
            wizard.set_phrase_result(language, "Подготовка микрофона…", False)

    def _observe_practice(self, snapshot: object) -> None:
        entry = self._practice
        if entry is None:
            return
        language, identifier = entry
        state = snapshot.state
        readiness = snapshot.readiness
        if readiness.session_id == identifier and state in {
            "LISTENING",
            "SPEECH_DETECTED",
            "TRANSCRIBING",
        }:
            if not self._practice_timer.isActive():
                self._practice_timer.start()
            return
        if (
            state in {"COMPLETED", "ERROR_RECOVERABLE", "ERROR_FATAL"}
            and readiness.session_id is None
        ):
            self._practice = None
            self._practice_timer.stop()
            result = snapshot.recognition_result
            text = (
                result.text
                if state == "COMPLETED" and result is not None
                else snapshot.error_message
            )
            self.signals.phrase_ready.emit(
                language, text, state == "COMPLETED" and bool(text.strip())
            )

    def _stop_practice(self) -> None:
        if (
            self._practice is not None
            and self.controller.snapshot().readiness.session_id == self._practice[1]
        ):
            self.controller.stop_dictation()

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
                "language": values.get("language", self.runtime.settings.dictation.language.value),
                "model_profile": "maximum_accuracy"
                if values.get("model_profile") == "accurate"
                else values.get("model_profile", "balanced"),
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
        if self._professional_window is not None:
            self._professional_window.flush_editor_draft()
        self._closing = True
        self.controller.begin_shutdown()
        if self.main.isVisible():
            self.main.update_snapshot(self.controller.snapshot())
            self.main.setWindowTitle("VoxType — завершение работы")
        self._pending_dictation_target = None
        if self._model_download_cancel is not None:
            self._model_download_cancel.set()
        if self._update_cancel is not None:
            self._update_cancel.set()
        self._stop_audio_test()
        self.timer.stop()
        self._idle_model_timer.stop()
        self._foreground_events.close()
        self.system_events.close()
        self._recovery_timer.stop()
        self._professional_timer.stop()
        self._practice_timer.stop()
        if self._diagnostics_window is not None:
            self._diagnostics_window.hide()
        if self._file_queue is not None:
            self._file_queue.begin_shutdown()
        self._heartbeat_timer.stop()
        self._heartbeat_stop.set()
        self.tray.show_message("VoxType", "Безопасно завершаю работу в фоне")

        self._finish_shutdown_in_background()
        return False

    def _finish_shutdown_in_background(self) -> None:
        def worker() -> None:
            try:
                if self._settings_writer is not None:
                    self._settings_writer.join(1.0)
                    if self._settings_writer.is_alive():
                        self.signals.shutdown_ready.emit(False)
                        return
                with self._background_lock:
                    pending = tuple(self._background_workers)
                deadline = time.monotonic() + 1.0
                for operation in pending:
                    operation.join(max(0.0, deadline - time.monotonic()))
                if any(operation.is_alive() for operation in pending):
                    self.signals.shutdown_ready.emit(False)
                    return
                if self._file_queue is not None:
                    self._file_queue.wait_for_worker(2)
                closed = self.runtime.close()
            except Exception:
                _LOGGER.exception("shutdown_worker_failed")
                closed = None
            self.signals.shutdown_ready.emit(closed)

        threading.Thread(target=worker, name="voxtype-shutdown", daemon=True).start()

    def _on_shutdown_ready(self, closed: bool | None) -> None:
        if closed is False:
            QTimer.singleShot(1000, self.main, self._finish_shutdown_in_background)
            return
        if closed is None:
            self._closing = False
            self.tray.show_message(
                "VoxType", "Ошибка завершения. Подробности в журнале, повторите выход"
            )
            return
        self._closed = True
        self.runtime.session_repository.close_current_thread()
        self._disconnect_state_listener()
        if self._event_dispatcher is not None:
            self._event_dispatcher.awake.disconnect(self._dispatch_awake)
            self._event_dispatcher.aboutToBlock.disconnect(self._dispatch_done)
        try:
            self.hotkeys.close()
        except OSError as exc:
            _LOGGER.warning("hotkey_shutdown_failed kind=%s", type(exc).__name__)
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


def run_desktop(
    runtime: RuntimeBundle | None = None,
    *,
    smoke_after_ms: int | None = None,
    verbose: bool = False,
    on_installer: Any = None,
) -> int:
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
    shell: DesktopApplication | None = None
    startup_view: MainWindow | None = None
    startup_signals = _WorkerSignals()
    if runtime is not None or smoke_after_ms is not None:
        shell = DesktopApplication(application, runtime or build_runtime())
        application.setProperty("voxtype_shell", shell)
    else:
        application.setQuitOnLastWindowClosed(False)
        apply_theme(application)
        startup_view = MainWindow(minimize_to_tray=False)
        startup_view.update_snapshot(
            UIState(
                state=AppStatus.STARTING,
                readiness=AppReadiness(
                    AppPhase.INITIALIZING, "initializing", "Подготовка VoxType", False, False
                ),
            )
        )
        startup_view.exit_requested.connect(application.quit)
        startup_restore = _StartupRestoreFilter(startup_view)
        application.installNativeEventFilter(startup_restore)
        startup_view.show()

        def attach(bundle: RuntimeBundle) -> None:
            nonlocal shell
            startup_view.exit_requested.disconnect(application.quit)
            application.removeNativeEventFilter(startup_restore)
            shell = DesktopApplication(application, bundle, main=startup_view)
            application.setProperty("voxtype_shell", shell)

        def startup_error(kind: str) -> None:
            from PySide6.QtWidgets import QMessageBox

            _LOGGER.error("runtime_initialization_failed kind=%s", kind)
            message = (
                "Эти настройки или база созданы более новой версией VoxType. Установите совместимую версию."
                if kind in {"UnsupportedSettingsSchema", "UnsupportedDatabaseSchema"}
                else failure_message("configuration_error")
            )
            QMessageBox.critical(startup_view, "VoxType", message)
            application.exit(1)

        def initialize_runtime() -> None:
            try:
                bundle = build_runtime(verbose=verbose)
            except Exception as exc:
                startup_signals.startup_failed.emit(type(exc).__name__)
            else:
                startup_signals.startup_ready.emit(bundle)

        startup_signals.startup_ready.connect(attach, Qt.ConnectionType.QueuedConnection)
        startup_signals.startup_failed.connect(startup_error, Qt.ConnectionType.QueuedConnection)
        QTimer.singleShot(
            0,
            lambda: threading.Thread(
                target=initialize_runtime,
                name="voxtype-runtime-initialization",
                daemon=True,
            ).start(),
        )
    smoke_result = smoke_after_ms is None
    if smoke_after_ms is not None:

        def finish_smoke() -> None:
            nonlocal smoke_result
            if shell is None:
                return
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
        if shell is not None:
            shell.controller.handle_ui_exception(exception)
        else:
            application.exit(1)

    sys.excepthook = ui_excepthook
    # Keep a Python reference for the full Qt event loop lifetime.
    try:
        _LOGGER.info(
            "startup_stage name=event_loop_enter total_ms=%.1f",
            (time.perf_counter() - started) * 1_000.0,
        )
        exit_code = int(application.exec())
        if (
            not exit_code
            and on_installer is not None
            and shell is not None
            and shell._closed
            and shell.installer_handoff
        ):
            on_installer(shell.installer_handoff)
        return exit_code if exit_code else (0 if smoke_result else 3)
    finally:
        _LOGGER.info(
            "startup_stage name=event_loop_exit total_ms=%.1f",
            (time.perf_counter() - started) * 1_000.0,
        )
        sys.excepthook = previous_excepthook


__all__ = ["DesktopApplication", "run_desktop"]
