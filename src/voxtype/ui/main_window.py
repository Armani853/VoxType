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
    from PySide6.QtCore import QEvent, QObject, Qt, Signal
    from PySide6.QtGui import QCloseEvent, QGuiApplication
    from PySide6.QtWidgets import (
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpacerItem,
        QVBoxLayout,
        QWidget,
    )

    from .theme import SPACING, create_app_icon, state_color
    from .widgets import (
        AudioLevelMeter,
        Card,
        ElidedLabel,
        MetricTile,
        SegmentedLanguageControl,
        StatusDot,
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
        history_requested = Signal()
        diagnostics_requested = Signal()
        onboarding_requested = Signal()
        retry_requested = Signal()
        hidden_to_tray = Signal()
        exit_requested = Signal()

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
            self.resize(980, 760)
            self.setMinimumSize(780, 600)
            self._build_ui()
            self._connect_ui()
            self.retranslate_ui()
            self.update_snapshot(self._state)
            if controller is not None:
                self.bind_controller(controller)

        def _build_ui(self) -> None:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setCentralWidget(scroll)
            canvas = QWidget(scroll)
            scroll.setWidget(canvas)
            scroll.viewport().installEventFilter(self)
            outer = QVBoxLayout(canvas)
            outer.setContentsMargins(
                SPACING.space_6,
                SPACING.space_6,
                SPACING.space_6,
                SPACING.space_6,
            )
            outer.setSpacing(SPACING.space_5)

            header = QHBoxLayout()
            title_column = QVBoxLayout()
            title_column.setSpacing(1)
            self.title_label = QLabel("VoxType", canvas)
            self.title_label.setProperty("heading", True)
            self.subtitle_label = QLabel("Локальная голосовая диктовка", canvas)
            self.subtitle_label.setProperty("muted", True)
            title_column.addWidget(self.title_label)
            title_column.addWidget(self.subtitle_label)
            header.addLayout(title_column)
            self.local_badge = QLabel("ЛОКАЛЬНО", canvas)
            self.local_badge.setObjectName("localBadge")
            self.local_badge.setProperty("statusBadge", "success")
            header.addWidget(self.local_badge)
            header.addStretch(1)
            self.history_button = self._header_button("История", "historyButton")
            self.diagnostics_button = self._header_button("Диагностика", "diagnosticsButton")
            self.settings_button = self._header_button("Настройки", "settingsButton")
            header.addWidget(self.history_button)
            header.addWidget(self.diagnostics_button)
            header.addWidget(self.settings_button)
            outer.addLayout(header)

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

            hero = Card(canvas, spacing=14)
            hero.setObjectName("statusCard")
            status_row = QHBoxLayout()
            self.status_dot = StatusDot(hero)
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
            self.status_detail_label = QLabel("", hero)
            self.status_detail_label.setProperty("muted", True)
            self.status_detail_label.setWordWrap(True)
            hero.content_layout.addWidget(self.status_detail_label)
            self.dictation_button = QPushButton("Начать диктовку", hero)
            self.dictation_button.setObjectName("dictationButton")
            self.dictation_button.setProperty("primary", True)
            self.dictation_button.setAccessibleDescription(
                "Запускает или останавливает текущую сессию диктовки"
            )
            hero.content_layout.addWidget(self.dictation_button)
            self.hotkey_label = QLabel("Ctrl+Alt+Space", hero)
            self.hotkey_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.hotkey_label.setProperty("keyHint", True)
            hero.content_layout.addWidget(
                self.hotkey_label,
                0,
                Qt.AlignmentFlag.AlignHCenter,
            )
            outer.addWidget(hero)

            device_grid = QGridLayout()
            device_grid.setHorizontalSpacing(16)
            device_grid.setVerticalSpacing(16)
            microphone = Card(canvas, spacing=7)
            microphone.setObjectName("microphoneCard")
            self.microphone_title = QLabel("Микрофон", microphone)
            self.microphone_title.setProperty("caption", True)
            self.microphone_label = ElidedLabel("Системный микрофон", microphone)
            self.microphone_label.setObjectName("microphoneLabel")
            self.microphone_label.setProperty("sectionHeading", True)
            self.audio_meter = AudioLevelMeter(microphone)
            self.audio_meter.setObjectName("audioMeter")
            self.audio_hint = QLabel("Нет клиппинга", microphone)
            self.audio_hint.setProperty("caption", True)
            microphone.content_layout.addWidget(self.microphone_title)
            microphone.content_layout.addWidget(self.microphone_label)
            microphone.content_layout.addWidget(self.audio_meter)
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
            outer.addLayout(metrics)

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
            fragment.content_layout.addWidget(self.fragment_label)
            outer.addWidget(fragment)
            outer.addItem(
                QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            )

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            central = self.centralWidget()
            if (
                not self._first_content_paint_seen
                and isinstance(central, QScrollArea)
                and watched is central.viewport()
                and event.type() is QEvent.Type.Paint
            ):
                self._first_content_paint_seen = True
                canvas = central.widget()
                _LOGGER.info(
                    "first_content_paint elapsed_ms=%.1f viewport=%dx%d canvas=%dx%d visible=%s",
                    (time.perf_counter() - self._constructed_at) * 1_000.0,
                    central.viewport().width(),
                    central.viewport().height(),
                    canvas.width() if canvas is not None else 0,
                    canvas.height() if canvas is not None else 0,
                    self.isVisible(),
                )
            return super().eventFilter(watched, event)

        def _header_button(self, text: str, name: str) -> QPushButton:
            button = QPushButton(text, self)
            button.setObjectName(name)
            button.setProperty("flat", True)
            return button

        def _connect_ui(self) -> None:
            self.dictation_button.clicked.connect(self._on_dictation_button_clicked)
            self.language_control.language_changed.connect(self._on_language_selected)
            self.settings_button.clicked.connect(self.settings_requested)
            self.history_button.clicked.connect(self.history_requested)
            self.diagnostics_button.clicked.connect(self.diagnostics_requested)
            self.error_diagnostics_button.clicked.connect(self.diagnostics_requested)
            self.retry_button.clicked.connect(self.retry_requested)
            self.copy_text_button.clicked.connect(self._copy_accumulated_text)

        def _on_dictation_button_clicked(self) -> None:
            if not self._model_available and self._state.state in {
                AppStatus.IDLE,
                AppStatus.COMPLETED,
                AppStatus.ERROR_RECOVERABLE,
            }:
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
            state = snapshot_from(snapshot)
            self._state = state
            key, color_role = _STATUS_PRESENTATION[state.state]
            needs_model = not self._model_available and state.state in {
                AppStatus.IDLE,
                AppStatus.COMPLETED,
            }
            awaiting_target = (
                state.state is AppStatus.COMPLETED
                and state.delivery_status == "queued_target_inactive"
            )
            self.status_dot.set_color(
                state_color("warning" if needs_model or awaiting_target else color_role)
            )
            self.status_label.setText(
                ("Требуется модель" if self._interface_language == "ru" else "Model required")
                if needs_model
                else (
                    "Ожидает вставки" if self._interface_language == "ru" else "Waiting to insert"
                )
                if awaiting_target
                else ui_text(key, self._interface_language)
            )
            detail = state.error_message or state.status_message
            if awaiting_target and not state.error_message:
                detail = (
                    "Вернитесь в целевое окно — сохранённый текст вставится автоматически"
                    if self._interface_language == "ru"
                    else "Return to the target window to insert the saved text automatically"
                )
            if not detail:
                detail = self._default_status_detail(state)
            self.status_detail_label.setText(detail)
            self.language_control.set_language(state.language.value)
            self.microphone_label.setText(state.microphone_name or "—")
            self.microphone_label.setToolTip(state.microphone_name)
            self.audio_meter.set_level(rms_to_meter(state.input_rms), state.clipping_percentage)
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
            self.dictation_button.setText(
                ("Настроить модель" if self._interface_language == "ru" else "Set up model")
                if needs_model
                else ui_text("stop" if active else "start", self._interface_language)
            )
            if self.dictation_button.property("danger") != active:
                self.dictation_button.setProperty("danger", active)
                self._refresh_dynamic_style(self.dictation_button)
            self.dictation_button.setEnabled(
                state.state
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
            self.error_banner.setVisible(has_error)
            if has_error:
                self.error_label.setText(
                    state.error_message
                    or "Не удалось продолжить диктовку. Текст сохранён локально."
                )
                self.retry_button.setVisible(state.state is AppStatus.ERROR_RECOVERABLE)
                self.copy_text_button.setEnabled(bool(state.last_committed_text))

        def _default_status_detail(self, state: UIState) -> str:
            if not self._model_available and state.state in {AppStatus.IDLE, AppStatus.COMPLETED}:
                return (
                    "Сначала установите локальную модель в мастере настройки"
                    if self._interface_language == "ru"
                    else "Install a local model in the setup wizard first"
                )
            if state.state is AppStatus.IDLE:
                return f"Нажмите кнопку или {self._toggle_hotkey}, чтобы начать"
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
            self.title_label.setText(ui_text("app_name", locale))
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
            self.hotkey_label.setText(self._toggle_hotkey)

        def set_toggle_hotkey(self, specification: str) -> None:
            self._toggle_hotkey = specification or "Ctrl+Alt+Space"
            self.hotkey_label.setText(self._toggle_hotkey)
            self.update_snapshot(self._state)

        def set_minimize_to_tray(self, enabled: bool) -> None:
            self._minimize_to_tray = bool(enabled)

        def force_close(self) -> None:
            """Close for application shutdown, bypassing minimize-to-tray."""

            self._allow_close = True
            self.close()

        def closeEvent(self, event: QCloseEvent) -> None:
            if self._allow_close:
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
