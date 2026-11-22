"""The primary VoxType desktop window.

This module only renders state and emits user intent.  Audio capture and ASR
remain owned by the application controller.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .i18n import ui_text
from .models import (
    AppStatus,
    DictationLanguage,
    ModelProfile,
    UIState,
    format_backlog,
    format_duration,
    snapshot_from,
    text_preview,
)

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QByteArray, QEvent, QObject, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import QCloseEvent, QGuiApplication
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpacerItem,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )

    from .desktop_shell import DesktopShell
    from .obsidian import (
        AnimatedStatusDot,
        IconProvider,
        VoxBusyIndicator,
        VoxButton,
        VoxCard,
        VoxSectionHeader,
        VoxShortcutBadge,
        WaveformWidget,
    )
    from .theme import create_app_icon, state_color, theme_manager
    from .widgets import (
        Card,
        MetricTile,
        SegmentedLanguageControl,
        rms_to_meter,
    )

    _STATUS_PRESENTATION: dict[AppStatus, tuple[str, str]] = {
        AppStatus.STARTING: ("starting", "neutral"),
        AppStatus.MODEL_LOADING: ("model_loading", "primary"),
        AppStatus.IDLE: ("ready", "success"),
        AppStatus.LISTENING: ("listening", "primary"),
        AppStatus.SPEECH_DETECTED: ("speech", "success"),
        AppStatus.TRANSCRIBING: ("transcribing", "primary"),
        AppStatus.PAUSED_TARGET_LOST: ("target_lost", "warning"),
        AppStatus.FINISHING: ("finishing", "primary"),
        AppStatus.COMPLETED: ("ready", "success"),
        AppStatus.ERROR_RECOVERABLE: ("recoverable_error", "warning"),
        AppStatus.ERROR_FATAL: ("fatal_error", "error"),
        AppStatus.SHUTTING_DOWN: ("shutting_down", "neutral"),
        AppStatus.PAUSED: ("paused", "warning"),
    }
    _LOGGER = logging.getLogger("voxtype.ui.main_window")

    class MainWindow(QMainWindow):
        dictation_toggle_requested = Signal()
        pause_toggle_requested = Signal()
        language_change_requested = Signal(str)
        settings_requested = Signal()
        professional_requested = Signal()
        history_requested = Signal()
        diagnostics_requested = Signal()
        onboarding_requested = Signal()
        retry_requested = Signal()
        hidden_to_tray = Signal()
        exit_requested = Signal()
        copy_result_requested = Signal()
        edit_result_requested = Signal()
        copy_pending_requested = Signal()
        insert_result_requested = Signal()
        cancel_processing_requested = Signal()
        clear_result_requested = Signal()
        select_target_requested = Signal()

        def __init__(
            self,
            controller: object | None = None,
            parent: QWidget | None = None,
            *,
            interface_language: str = "ru",
            minimize_to_tray: bool = True,
        ) -> None:
            super().__init__(parent)
            self._interface_language = "en" if interface_language == "en" else "ru"
            self._state = UIState()
            self._minimize_to_tray = bool(minimize_to_tray)
            self._allow_close = False
            self._toggle_hotkey = "Ctrl+Alt+Space"
            self._model_available = True
            self._controller: object | None = None
            self._controller_connections: list[tuple[Any, Callable[..., Any]]] = []
            self._constructed_at = time.perf_counter()
            self._first_content_paint_seen = False
            self.setObjectName("mainWindow")
            self.setWindowTitle("VoxType — локальная диктовка")
            self.setWindowIcon(create_app_icon())
            self.resize(1100, 720)
            self.setMinimumSize(760, 560)
            self._geometry_store = None
            self._observed_result = ""
            self._build_ui()
            self._connect_ui()
            self.retranslate_ui()
            self.update_snapshot(self._state)
            if controller is not None:
                self.bind_controller(controller)

        def _build_ui(self) -> None:
            self.shell = DesktopShell(self)
            self.setCentralWidget(self.shell)
            scroll = QScrollArea(self.shell)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            canvas = QWidget(scroll)
            scroll.setWidget(canvas)
            self._dashboard_scroll = scroll
            scroll.viewport().installEventFilter(self)
            self.shell.install_page("home", scroll)
            self.shell.install_page("dictation", scroll)
            self.shell.show_page("home")
            outer = QVBoxLayout(canvas)
            outer.setContentsMargins(28, 24, 28, 20)
            outer.setSpacing(16)

            self.title_label = VoxSectionHeader("Готов к диктовке", canvas, role="hero")
            outer.addWidget(self.title_label)
            self.subtitle_label = QLabel("Профиль по умолчанию", canvas)
            self.subtitle_label.setProperty("muted", True)
            outer.addWidget(self.subtitle_label)
            self.local_badge = self.shell.privacy
            self.history_button = self._header_button("История", "historyButton")
            self.diagnostics_button = self._header_button("Диагностика", "diagnosticsButton")
            self.settings_button = self._header_button("Настройки", "settingsButton")
            self.professional_button = self._header_button(
                "Профили и инструменты", "professionalButton"
            )
            for button in (
                self.history_button,
                self.diagnostics_button,
                self.settings_button,
                self.professional_button,
            ):
                button.hide()

            self.error_banner = QFrame(canvas)
            self.error_banner.setObjectName("errorBanner")
            self.error_banner.setProperty("warningBanner", True)
            banner_layout = QHBoxLayout(self.error_banner)
            banner_layout.setContentsMargins(14, 10, 12, 10)
            self.error_label = QLabel("", self.error_banner)
            self.error_label.setWordWrap(True)
            banner_layout.addWidget(self.error_label, 1)
            self.copy_text_button = QPushButton("Скопировать текст", self.error_banner)
            self.retry_button = QPushButton("Повторить", self.error_banner)
            self.error_diagnostics_button = QPushButton("Диагностика", self.error_banner)
            banner_layout.addWidget(self.copy_text_button)
            banner_layout.addWidget(self.retry_button)
            banner_layout.addWidget(self.error_diagnostics_button)
            outer.addWidget(self.error_banner)

            hero = VoxCard(canvas, spacing=10)
            hero.setMaximumWidth(660)
            hero.setObjectName("statusCard")
            status_row = QHBoxLayout()
            self.status_dot = AnimatedStatusDot(hero)
            status_row.addWidget(self.status_dot)
            self.status_label = QLabel("Готово", hero)
            self.status_label.setObjectName("statusLabel")
            self.status_label.setProperty("heading", True)
            status_row.addWidget(self.status_label)
            status_row.addStretch(1)
            self.language_control = SegmentedLanguageControl(hero)
            self.language_control.setObjectName("languageControl")
            status_row.addWidget(self.language_control)
            hero.content_layout.addLayout(status_row)
            self.recognition_language_label = QLabel(hero)
            self.recognition_language_label.setProperty("caption", True)
            self.recognition_language_label.setWordWrap(True)
            hero.content_layout.addWidget(self.recognition_language_label)
            self.status_detail_label = QLabel("", hero)
            self.status_detail_label.setProperty("muted", True)
            self.status_detail_label.setWordWrap(True)
            self.status_detail_label.setMinimumHeight(32)
            hero.content_layout.addWidget(self.status_detail_label)
            self.dictation_button = VoxButton("", hero, variant="primary")
            self.dictation_button.setObjectName("dictationButton")
            self.dictation_button.setProperty("voiceControl", True)
            self.dictation_button.setFixedSize(72, 72)
            self.dictation_button.setIconSize(QSize(24, 24))
            self.dictation_button.setAccessibleDescription("Запускает или останавливает диктовку")
            from .obsidian import VoiceHalo

            self.voice_halo = VoiceHalo(self.dictation_button, hero)
            hero.content_layout.addWidget(self.voice_halo, 0, Qt.AlignmentFlag.AlignHCenter)
            self.voice_action_label = QLabel("Начать диктовку", hero)
            hero.content_layout.addWidget(self.voice_action_label, 0, Qt.AlignmentFlag.AlignHCenter)
            self.voice_activity = QStackedWidget(hero)
            self.voice_activity.setFixedHeight(42)
            self.audio_meter = WaveformWidget(self.voice_activity)
            self.audio_meter.setObjectName("audioMeter")
            self.voice_activity.addWidget(self.audio_meter)
            processing_panel = QWidget(self.voice_activity)
            processing_layout = QVBoxLayout(processing_panel)
            processing_layout.setContentsMargins(48, 0, 48, 0)
            processing_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.processing = VoxBusyIndicator("Обрабатываем речь", processing_panel)
            processing_layout.addWidget(self.processing)
            self.voice_activity.addWidget(processing_panel)
            hero.content_layout.addWidget(self.voice_activity)
            self.live_panel = QWidget(hero)
            live_layout = QVBoxLayout(self.live_panel)
            live_layout.setContentsMargins(0, 0, 0, 0)
            self.live_stable = QLabel(self.live_panel)
            self.live_unstable = QLabel(self.live_panel)
            self.live_unstable.setProperty("muted", True)
            for label in (self.live_stable, self.live_unstable):
                label.setWordWrap(True)
                label.setTextFormat(Qt.TextFormat.PlainText)
                live_layout.addWidget(label)
            self.live_panel.setAccessibleName("Предварительный текст")
            hero.content_layout.addWidget(self.live_panel)
            self.live_panel.hide()
            self._live_key: tuple[str, int] | None = None
            self.cancel_button = VoxButton("Отменить обработку", hero)
            size_policy = self.cancel_button.sizePolicy()
            size_policy.setRetainSizeWhenHidden(True)
            self.cancel_button.setSizePolicy(size_policy)
            self.cancel_button.hide()
            self.cancel_button.clicked.connect(self.cancel_processing_requested)
            hero.content_layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)
            self.hotkey_label = VoxShortcutBadge(self._toggle_hotkey, hero)
            hero.content_layout.addWidget(self.hotkey_label, 0, Qt.AlignmentFlag.AlignHCenter)
            centered = QHBoxLayout()
            centered.addStretch()
            centered.addWidget(hero, 1)
            centered.addStretch()
            outer.addLayout(centered)
            theme_manager(QApplication.instance()).theme_changed.connect(self._refresh_voice_icon)

            device_grid = QGridLayout()
            device_grid.setHorizontalSpacing(16)
            device_grid.setVerticalSpacing(16)
            microphone = Card(canvas, spacing=7)
            microphone.setObjectName("microphoneCard")
            self.microphone_title = QLabel("Микрофон", microphone)
            self.microphone_title.setProperty("caption", True)
            self.microphone_label = QLabel("Системный микрофон", microphone)
            self.microphone_label.setObjectName("microphoneLabel")
            self.microphone_label.setProperty("sectionHeading", True)
            self.microphone_label.setWordWrap(True)
            self.audio_hint = QLabel("Нет клиппинга", microphone)
            self.audio_hint.setProperty("caption", True)
            microphone.content_layout.addWidget(self.microphone_title)
            microphone.content_layout.addWidget(self.microphone_label)
            microphone.content_layout.addWidget(self.audio_hint)
            device_grid.addWidget(microphone, 0, 0)

            model = Card(canvas, spacing=7)
            model.setObjectName("modelCard")
            self.model_title = QLabel("Режим", model)
            self.model_title.setProperty("caption", True)
            self.model_label = QLabel("Баланс", model)
            self.model_label.setObjectName("modelLabel")
            self.model_label.setProperty("sectionHeading", True)
            self.model_label.setWordWrap(True)
            self.profile_label = QLabel("Локальная модель: не загружена", model)
            self.profile_label.setProperty("caption", True)
            model.content_layout.addWidget(self.model_title)
            model.content_layout.addWidget(self.model_label)
            model.content_layout.addWidget(self.profile_label)
            model.content_layout.addStretch(1)
            device_grid.addWidget(model, 0, 1)
            device_grid.setColumnStretch(0, 3)
            device_grid.setColumnStretch(1, 2)
            outer.addLayout(device_grid)

            metrics = QHBoxLayout()
            metrics.setSpacing(12)
            self.duration_tile = MetricTile("Длительность", "00:00:00", canvas)
            self.duration_tile.setObjectName("durationTile")
            self.backlog_tile = MetricTile("Очередь аудио", "0.0 с", canvas)
            self.backlog_tile.setObjectName("backlogTile")
            self.target_tile = MetricTile("Цель", "—", canvas)
            self.target_tile.setObjectName("targetTile")
            metrics.addWidget(self.duration_tile, 1)
            metrics.addWidget(self.backlog_tile, 1)
            metrics.addWidget(self.target_tile, 2)
            self.metrics_panel = QWidget(canvas)
            self.metrics_panel.setLayout(metrics)
            outer.addWidget(self.metrics_panel)
            self.metrics_panel.hide()

            fragment = Card(canvas, spacing=8)
            fragment.setObjectName("lastFragmentCard")
            self.fragment_title = QLabel("Последний фрагмент", fragment)
            self.fragment_title.setProperty("caption", True)
            self.fragment_label = QLabel("Распознанный текст появится здесь", fragment)
            self.fragment_label.setObjectName("lastFragmentLabel")
            self.fragment_label.setWordWrap(True)
            self.fragment_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.fragment_label.setMinimumHeight(42)
            fragment.content_layout.addWidget(self.fragment_title)
            self.fragment_label.hide()
            self.result_editor = QPlainTextEdit(fragment)
            self.result_editor.setAccessibleName("Последний окончательный текст")
            self.result_editor.setPlaceholderText("Окончательный текст появится после диктовки")
            self.result_editor.setMaximumHeight(130)
            self.result_editor.setReadOnly(True)
            self.result_editor.textChanged.connect(self._update_result_actions)
            fragment.content_layout.addWidget(self.result_editor)
            result_actions = QHBoxLayout()
            self.result_copy = VoxButton("Копировать", fragment)
            self.result_copy_pending = VoxButton("Скопировать ожидающее", fragment)
            self.result_copy_pending.clicked.connect(self.copy_pending_requested)
            self.result_copy_pending.hide()
            self.result_edit = VoxButton("Исправить текст", fragment)
            self.result_edit.setEnabled(False)
            self.result_edit.clicked.connect(self.edit_result_requested)
            self.result_insert = VoxButton("Повторить вставку", fragment)
            self.result_clear = VoxButton("Очистить", fragment, variant="flat")
            self.result_target = VoxButton("Выбрать окно", fragment)
            self.result_clear.clicked.connect(self.clear_result_requested)
            self.result_target.clicked.connect(self.select_target_requested)
            history = VoxButton("Открыть историю", fragment, variant="flat")
            self.result_copy.clicked.connect(self.copy_result_requested)
            self.result_insert.clicked.connect(self.insert_result_requested)
            history.clicked.connect(self.history_requested)
            for button in (
                self.result_copy,
                self.result_copy_pending,
                self.result_edit,
                self.result_insert,
                self.result_clear,
                self.result_target,
                history,
            ):
                result_actions.addWidget(button)
            result_actions.addStretch()
            fragment.content_layout.addLayout(result_actions)
            outer.addWidget(fragment)
            outer.addItem(
                QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            )

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            dashboard = getattr(self, "_dashboard_scroll", None)
            if (
                not self._first_content_paint_seen
                and dashboard is not None
                and watched is dashboard.viewport()
                and event.type() is QEvent.Type.Paint
            ):
                self._first_content_paint_seen = True
                canvas = dashboard.widget()
                _LOGGER.info(
                    "first_content_paint elapsed_ms=%.1f viewport=%dx%d canvas=%dx%d visible=%s",
                    (time.perf_counter() - self._constructed_at) * 1_000.0,
                    dashboard.viewport().width(),
                    dashboard.viewport().height(),
                    canvas.width() if canvas is not None else 0,
                    canvas.height() if canvas is not None else 0,
                    self.isVisible(),
                )
            return super().eventFilter(watched, event)

        def _refresh_voice_icon(self, _theme: object = None) -> None:
            icon = "stop" if self.dictation_button.property("voiceActive") else "mic"
            token = (
                "canvas"
                if theme_manager(QApplication.instance()).current_theme.value == "dark"
                else "surface"
            )
            if self.dictation_button.property("voiceMode") == "processing":
                token = "text_secondary"
            self.dictation_button.setIcon(
                IconProvider.icon(
                    icon, 24, self.dictation_button.devicePixelRatioF(), color_token=token
                )
            )

        def _update_result_actions(self) -> None:
            if not hasattr(self, "result_target"):
                return
            available = (
                bool(self.result_editor.toPlainText().strip()) and not self._state.state.active
            )
            for button in (
                self.result_copy,
                self.result_insert,
                self.result_clear,
                self.result_target,
            ):
                button.setEnabled(available)

        def result_text(self) -> str:
            return self.result_editor.toPlainText()

        def set_profile_name(self, name: str) -> None:
            if self.shell.profile.text() == name:
                return
            self.shell.profile.setText(name)
            self.subtitle_label.setText(
                ("Профиль · " if self._interface_language == "ru" else "Profile · ") + name
            )

        def set_geometry_store(self, store: object) -> None:
            self._geometry_store = store
            saved = store.value("mainWindow/geometry")
            if isinstance(saved, QByteArray):
                self.restoreGeometry(saved)
            self._geometry_timer = QTimer(self, interval=350, singleShot=True)
            self._geometry_timer.timeout.connect(self._save_geometry)

        def _save_geometry(self) -> None:
            if self._geometry_store is not None:
                self._geometry_store.setValue("mainWindow/geometry", self.saveGeometry())

        def moveEvent(self, event: object) -> None:
            super().moveEvent(event)
            if hasattr(self, "_geometry_timer"):
                self._geometry_timer.start()

        def resizeEvent(self, event: object) -> None:
            super().resizeEvent(event)
            if hasattr(self, "_geometry_timer"):
                self._geometry_timer.start()

        def _header_button(self, text: str, name: str) -> QPushButton:
            button = QPushButton(text, self)
            button.setObjectName(name)
            button.setProperty("flat", True)
            return button

        def _connect_ui(self) -> None:
            self.dictation_button.clicked.connect(self._on_dictation_button_clicked)
            self.language_control.language_changed.connect(self._on_language_selected)
            self.settings_button.clicked.connect(self.settings_requested)
            self.professional_button.clicked.connect(self.professional_requested)
            self.history_button.clicked.connect(self.history_requested)
            self.diagnostics_button.clicked.connect(self.diagnostics_requested)
            self.error_diagnostics_button.clicked.connect(self.diagnostics_requested)
            self.retry_button.clicked.connect(self.retry_requested)
            self.copy_text_button.clicked.connect(self._copy_accumulated_text)

        def _on_dictation_button_clicked(self) -> None:
            if (
                self._state.readiness is not None
                and self._state.readiness.reason == "model_missing"
            ) or (
                not self._model_available
                and self._state.state
                in {
                    AppStatus.IDLE,
                    AppStatus.COMPLETED,
                    AppStatus.ERROR_RECOVERABLE,
                }
            ):
                self.onboarding_requested.emit()
            else:
                self.dictation_toggle_requested.emit()

        def set_model_available(self, available: bool) -> None:
            self._model_available = bool(available)
            self.update_snapshot(self._state)

        def bind_controller(self, controller: object) -> None:
            """Bind the four stable controller commands without importing core."""

            self.unbind_controller()
            self._controller = controller
            bindings = (
                (self.dictation_toggle_requested, "toggle_dictation"),
                (self.pause_toggle_requested, "toggle_pause"),
            )
            for signal, method_name in bindings:
                method = getattr(controller, method_name, None)
                if callable(method):
                    signal.connect(method)
                    self._controller_connections.append((signal, method))
            switch = getattr(controller, "switch_language", None)
            if callable(switch):

                def callback(_language: str) -> None:
                    switch()

                self.language_change_requested.connect(callback)
                self._controller_connections.append((self.language_change_requested, callback))

        def unbind_controller(self) -> None:
            for signal, callback in self._controller_connections:
                try:
                    signal.disconnect(callback)
                except (RuntimeError, TypeError):
                    continue
            self._controller_connections.clear()
            self._controller = None

        def update_snapshot(self, snapshot: UIState | object) -> None:
            recognition = getattr(snapshot, "recognition_result", None)
            self.result_copy_pending.setVisible(
                bool(
                    getattr(snapshot, "pending_insertion", False)
                    and getattr(snapshot, "state", "") in {"COMPLETED", "IDLE", "ERROR_RECOVERABLE"}
                )
            )
            self.result_edit.setEnabled(
                bool(
                    recognition
                    and recognition.raw_text
                    and getattr(snapshot, "full_transcript", "")
                )
            )
            live = getattr(snapshot, "live_transcript", None)
            if live is None or getattr(snapshot, "live_final", None) is not None:
                self.live_panel.hide()
            elif (
                self._live_key is None
                or self._live_key[0] != live.session_id
                or live.revision > self._live_key[1]
            ):
                self._live_key = (live.session_id, live.revision)
                stable = " ".join(segment.text for segment in live.stable_segments)
                for label, text in (
                    (self.live_stable, stable),
                    (self.live_unstable, live.unstable_text),
                ):
                    label.setVisible(bool(text))
                    if label.text() != text:
                        label.setText(text)
                self.live_panel.setVisible(bool(stable or live.unstable_text))
            state = snapshot_from(snapshot)
            self._state = state
            key, color_role = _STATUS_PRESENTATION[state.state]
            needs_model = not self._model_available and state.state in {
                AppStatus.IDLE,
                AppStatus.COMPLETED,
            }
            if state.readiness is not None:
                needs_model = state.readiness.reason == "model_missing"
            awaiting_target = (
                state.state is AppStatus.COMPLETED
                and state.delivery_status == "queued_target_inactive"
            )
            no_result = state.state is AppStatus.COMPLETED and state.delivery_status == "no_result"
            self.status_dot.set_color(
                state_color(
                    "warning" if needs_model or awaiting_target or no_result else color_role
                )
            )
            status_text = ui_text(key, self._interface_language)
            is_ru = self._interface_language == "ru"
            if needs_model:
                status_text = "Требуется модель" if is_ru else "Model required"
            elif awaiting_target:
                status_text = "Ожидает вставки" if is_ru else "Waiting to insert"
            elif no_result:
                status_text = "Нет результата" if is_ru else "No result"
            elif state.readiness is not None and state.readiness.reason == "microphone_missing":
                status_text = "Микрофон недоступен" if is_ru else "Microphone unavailable"
            self.status_label.setText(status_text)
            detail = state.error_message or state.status_message
            if state.readiness is not None and state.readiness.reason == "microphone_missing":
                detail = "Выберите доступный микрофон в настройках"
            if awaiting_target and not state.error_message:
                detail = (
                    "Нажмите «Повторить вставку» и вернитесь в целевое окно"
                    if self._interface_language == "ru"
                    else "Choose Insert again, then return to the target window"
                )
            if not detail:
                detail = self._default_status_detail(state)
            self.status_detail_label.setText(detail)
            selected_language = state.configured_language or state.language
            self.language_control.set_language(selected_language.value)
            current_label = "Язык текущей записи" if is_ru else "Current recording language"
            self.recognition_language_label.setText(
                f"{current_label} · {state.language.value.upper()}"
            )
            self.recognition_language_label.setVisible(
                state.state.active and state.configured_language is not None
            )
            self.microphone_label.setText(state.microphone_name or "—")
            self.microphone_label.setToolTip(state.microphone_name)
            recording = state.state in {
                AppStatus.LISTENING,
                AppStatus.SPEECH_DETECTED,
                AppStatus.TRANSCRIBING,
            }
            self.audio_meter.set_active(recording)
            self.voice_halo.set_recording(recording)
            if recording:
                self.audio_meter.set_level(rms_to_meter(state.input_rms))
            processing = state.state is AppStatus.FINISHING or (
                state.readiness is not None
                and state.readiness.phase.value == "processing"
                and not recording
            )
            self.voice_activity.setCurrentIndex(int(processing))
            self.cancel_button.setVisible(
                state.readiness.can_cancel
                if state.readiness is not None
                else state.state is AppStatus.FINISHING
            )
            self.metrics_panel.setVisible(self.shell.current == "dictation")
            quality = getattr(snapshot, "audio_quality", None)
            if quality is not None and state.state.active:
                if quality.clipping_detected:
                    hint = "Клиппинг: уменьшите усиление микрофона"
                elif quality.low_signal:
                    hint = "Речь тихая: говорите ближе к микрофону"
                else:
                    hint = f"Уровень {quality.rms_dbfs:.0f} dBFS"
                self.audio_hint.setText(hint)
            else:
                self.audio_hint.setText(self._audio_hint(state))
            self.model_label.setText(self._profile_text(state.model_profile))
            model_label = state.model_name or "не загружена"
            if not self._model_available:
                model_label += (
                    " — не установлена" if self._interface_language == "ru" else " — not installed"
                )
            self.profile_label.setText(f"Локальная модель: {model_label}")
            self.duration_tile.set_value(format_duration(state.session_elapsed_s))
            self.backlog_tile.set_value(format_backlog(state.queued_audio_seconds))
            self.target_tile.set_value(
                state.target_title or ui_text("no_target", self._interface_language),
                state.target_title,
            )
            self.fragment_label.setText(self._fragment_text(state))
            tertiary = not bool(state.last_committed_text or state.full_transcript)
            if self.fragment_label.property("tertiary") != tertiary:
                self.fragment_label.setProperty("tertiary", tertiary)
                self._refresh_dynamic_style(self.fragment_label)
            active = state.state.active
            self.voice_action_label.setText(
                ("Настроить модель" if self._interface_language == "ru" else "Set up model")
                if needs_model
                else ("Обрабатываем речь" if is_ru else "Processing speech")
                if processing
                else ui_text("stop" if active else "start", self._interface_language)
            )
            self.dictation_button.setAccessibleName(self.voice_action_label.text())
            self.dictation_button.setToolTip(self.voice_action_label.text())
            self.title_label.setText(
                ("Готов к диктовке" if is_ru else "Ready to dictate")
                if state.state is AppStatus.IDLE
                and not needs_model
                and (state.readiness is None or state.readiness.can_record)
                else status_text
            )
            self.shell.engine.setText(status_text)
            self.shell.model.setText(model_label)
            finalized = state.full_transcript if not active else ""
            if self._observed_result != finalized:
                self._observed_result = finalized
                self.result_editor.setPlainText(finalized)
            self.result_editor.setReadOnly(active)
            self._update_result_actions()
            mode = (
                "processing"
                if processing
                else "error"
                if state.state in {AppStatus.ERROR_RECOVERABLE, AppStatus.ERROR_FATAL}
                else "recording"
                if recording
                else "idle"
            )
            if (
                self.dictation_button.property("voiceMode") != mode
                or self.dictation_button.property("voiceActive") != active
            ):
                self.dictation_button.setProperty("voiceActive", active)
                self.dictation_button.set_voice_mode(mode)
                self._refresh_voice_icon()
            self.dictation_button.setEnabled(
                needs_model
                or (
                    state.readiness is not None
                    and (
                        state.readiness.can_record
                        or (active and state.state is not AppStatus.FINISHING)
                    )
                )
                if state.readiness is not None
                else state.state
                not in {
                    AppStatus.FINISHING,
                    AppStatus.SHUTTING_DOWN,
                    AppStatus.ERROR_FATAL,
                }
            )
            has_error = state.state in {
                AppStatus.ERROR_RECOVERABLE,
                AppStatus.ERROR_FATAL,
            } or (
                state.state in {AppStatus.FINISHING, AppStatus.PAUSED_TARGET_LOST}
                and bool(state.error_message)
            )
            missing_microphone = (
                state.readiness is not None and state.readiness.reason == "microphone_missing"
            )
            self.error_banner.setVisible(has_error or missing_microphone)
            if has_error or missing_microphone:
                self.error_label.setText(
                    "Микрофон недоступен. Подключите его и нажмите «Повторить»."
                    if missing_microphone
                    else state.error_message
                    or "Не удалось продолжить диктовку. Текст сохранён локально."
                )
                self.retry_button.setVisible(
                    missing_microphone
                    or (
                        state.readiness.can_retry
                        if state.readiness is not None
                        else state.state is AppStatus.ERROR_RECOVERABLE
                    )
                )
                self.copy_text_button.setEnabled(bool(state.last_committed_text))

        def _default_status_detail(self, state: UIState) -> str:
            if not self._model_available and state.state in {AppStatus.IDLE, AppStatus.COMPLETED}:
                return (
                    "Сначала установите локальную модель в мастере настройки"
                    if self._interface_language == "ru"
                    else "Install a local model in the setup wizard first"
                )
            if state.state is AppStatus.IDLE:
                return (
                    f"Нажмите кнопку или {self._toggle_hotkey}, чтобы начать"
                    if self._toggle_hotkey
                    else "Нажмите кнопку, чтобы начать"
                )
            if state.state in {AppStatus.LISTENING, AppStatus.SPEECH_DETECTED}:
                return state.target_title or "Говорите обычным голосом"
            if state.state is AppStatus.TRANSCRIBING:
                return ui_text("processing_ok", self._interface_language)
            if state.state is AppStatus.PAUSED_TARGET_LOST:
                return "Вернитесь в целевое приложение — накопленный текст не потерян"
            if state.state is AppStatus.FINISHING:
                return "Обрабатываю оставшееся аудио и сохраняю результат"
            if state.state is AppStatus.COMPLETED:
                return "Результат сохранён; его можно скопировать или вставить повторно"
            if state.state is AppStatus.PAUSED:
                return "Запись приостановлена"
            return "VoxType работает полностью на этом компьютере"

        @staticmethod
        def _profile_text(profile: ModelProfile) -> str:
            return {
                ModelProfile.FAST: "Быстро",
                ModelProfile.BALANCED: "Баланс",
                ModelProfile.ACCURATE: "Максимальная точность",
            }[profile]

        def _fragment_text(self, state: UIState) -> str:
            committed = text_preview(
                state.full_transcript
                if state.state is AppStatus.COMPLETED
                else state.last_committed_text
            )
            if committed:
                return committed
            if state.state in {AppStatus.LISTENING, AppStatus.SPEECH_DETECTED}:
                return "Слушаю…" if self._interface_language == "ru" else "Listening…"
            if state.state in {AppStatus.TRANSCRIBING, AppStatus.FINISHING}:
                return "Распознаю…" if self._interface_language == "ru" else "Transcribing…"
            return ui_text("no_fragment", self._interface_language)

        @staticmethod
        def _audio_hint(state: UIState) -> str:
            if state.clipping_percentage > 0.1 or state.input_rms >= 0.98:
                return "Слишком громко — обнаружен клиппинг"
            if state.state.active and state.input_rms < 0.015:
                return "Сигнал очень тихий"
            if state.input_rms > 0.75:
                return "Громкий, но допустимый уровень"
            return "Уровень в норме"

        @staticmethod
        def _refresh_dynamic_style(widget: QWidget) -> None:
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

        def _on_language_selected(self, language: str) -> None:
            chosen = DictationLanguage.coerce(language)
            if chosen is self._state.language:
                return
            self.language_change_requested.emit(chosen.value)

        def _copy_accumulated_text(self) -> None:
            if self._state.last_committed_text:
                QGuiApplication.clipboard().setText(self._state.last_committed_text)

        def set_interface_language(self, locale: str) -> None:
            self._interface_language = "en" if locale == "en" else "ru"
            self.retranslate_ui()
            self.update_snapshot(self._state)

        def retranslate_ui(self) -> None:
            locale = self._interface_language
            self.shell.set_locale(locale)
            self.local_badge.setText(ui_text("local_badge", locale))
            self.history_button.setText(ui_text("history", locale))
            self.diagnostics_button.setText(ui_text("diagnostics", locale))
            self.settings_button.setText(ui_text("settings", locale))
            self.microphone_title.setText(ui_text("microphone", locale))
            self.model_title.setText("Режим" if locale == "ru" else "Mode")
            self.duration_tile.title_label.setText(ui_text("duration", locale))
            self.backlog_tile.title_label.setText(ui_text("backlog", locale))
            self.target_tile.title_label.setText(ui_text("target", locale))
            self.fragment_title.setText(ui_text("last_fragment", locale))
            self.hotkey_label.setText(getattr(self, "_dictation_mode_hint", self._toggle_hotkey))

        def set_toggle_hotkey(self, specification: str, mode_label: str = "") -> None:
            self._toggle_hotkey = specification
            self._dictation_mode_hint = " · ".join(filter(None, (mode_label, specification)))
            self.hotkey_label.setText(self._dictation_mode_hint)
            self.update_snapshot(self._state)

        def set_minimize_to_tray(self, enabled: bool) -> None:
            self._minimize_to_tray = bool(enabled)

        def force_close(self) -> None:
            """Close for application shutdown, bypassing minimize-to-tray."""

            self._allow_close = True
            self.close()

        def closeEvent(self, event: QCloseEvent) -> None:
            if self._allow_close:
                self._save_geometry()
                self.unbind_controller()
                event.accept()
                return
            if self._minimize_to_tray and not self._allow_close:
                event.ignore()
                self.hide()
                self.hidden_to_tray.emit()
                return
            # The application owns asynchronous shutdown; closing just the top-level
            # widget would leave a hidden process because quitOnLastWindowClosed is off.
            event.ignore()
            self.exit_requested.emit()

else:

    class MainWindow:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
