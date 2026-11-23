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
from .presentation import (
    CaptureState,
    DictationPresentation,
    DictationPresentationResolver,
    provisional_tail,
)

_LOGGER = logging.getLogger(__name__)

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QByteArray, QEvent, QObject, QSettings, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import (
        QCloseEvent,
        QColor,
        QGuiApplication,
        QMoveEvent,
        QResizeEvent,
        QTextCharFormat,
        QTextCursor,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
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

    class MainWindow(QMainWindow):
        first_paint = Signal(float)
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
        save_result_requested = Signal()
        audio_settings_requested = Signal()
        overlay_requested = Signal()
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
            self.first_paint_seconds: float | None = None
            self.setObjectName("mainWindow")
            self.setWindowTitle("VoxType — локальная диктовка")
            self.setWindowIcon(create_app_icon())
            self.resize(1100, 720)
            self.setMinimumSize(760, 560)
            self._geometry_store: QSettings | None = None
            self._observed_result = ""
            self._live_source: Callable[[str, int, int], str] | None = None
            self._live_document_session: str | None = None
            self._live_cursor_offset = 0
            self._show_live_provisional = True
            self._live_auto_scroll = True
            self._editing_live = False
            self._build_ui()
            self._connect_ui()
            self.retranslate_ui()
            self.update_snapshot(getattr(self, "_last_snapshot", self._state))
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
            self.title_label.hide()
            self.subtitle_label = QLabel("Профиль по умолчанию", canvas)
            self.subtitle_label.setProperty("muted", True)
            self.subtitle_label.hide()
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
            self.copy_text_button = VoxButton("Скопировать текст", self.error_banner)
            self.retry_button = VoxButton("Повторить", self.error_banner)
            self.error_diagnostics_button = VoxButton("Диагностика", self.error_banner)
            banner_layout.addWidget(self.copy_text_button)
            banner_layout.addWidget(self.retry_button)
            banner_layout.addWidget(self.error_diagnostics_button)
            self.choose_microphone_button = VoxButton("Выбрать другой", self.error_banner)
            self.choose_microphone_button.clicked.connect(self.audio_settings_requested)
            banner_layout.addWidget(self.choose_microphone_button)
            outer.addWidget(self.error_banner)

            hero = VoxCard(canvas, spacing=10)
            hero.setObjectName("statusCard")
            status_row = QHBoxLayout()
            self.status_dot = AnimatedStatusDot(hero)
            status_row.addWidget(self.status_dot)
            self.status_label = QLabel("Готово", hero)
            self.status_label.setObjectName("statusLabel")
            self.status_label.setProperty("heading", True)
            status_row.addWidget(self.status_label)
            status_row.addStretch(1)
            self.elapsed_label = QLabel("00:00:00", hero)
            self.elapsed_label.setObjectName("dictationElapsed")
            status_row.addWidget(self.elapsed_label)
            self.session_profile_label = QLabel(hero)
            self.session_profile_label.setProperty("caption", True)
            status_row.addWidget(self.session_profile_label)
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
            self.status_detail_label.hide()
            self.dictation_button = VoxButton("Начать диктовку", hero, variant="primary")
            self.dictation_button.setObjectName("dictationButton")
            self.dictation_button.setIconSize(QSize(18, 18))
            self.dictation_button.setAccessibleDescription("Запускает или останавливает диктовку")
            actions_card = VoxCard(canvas, spacing=8)
            record_actions = QHBoxLayout()
            record_actions.addWidget(self.dictation_button)
            self.voice_action_label = QLabel("Начать диктовку", actions_card)
            self.voice_action_label.hide()
            self.voice_activity = QStackedWidget(hero)
            self.voice_activity.setMinimumWidth(88)
            self.voice_activity.setMaximumWidth(180)
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
            self._live_key: tuple[str, int] | None = None
            self._preview_start: int | None = None
            self._preview_text = ""
            self.cancel_button = VoxButton("Отменить обработку", actions_card)
            self.cancel_button.hide()
            self.cancel_button.clicked.connect(self.cancel_processing_requested)
            record_actions.addWidget(self.cancel_button)
            record_actions.addStretch()
            self.hotkey_label = VoxShortcutBadge(self._toggle_hotkey, actions_card)
            record_actions.addWidget(self.hotkey_label)
            overlay_button = VoxButton("Мини-панель", actions_card, variant="flat")
            overlay_button.clicked.connect(self.overlay_requested)
            record_actions.addWidget(overlay_button)
            actions_card.content_layout.addLayout(record_actions)
            outer.addWidget(hero)
            theme_manager(QApplication.instance()).theme_changed.connect(self._refresh_voice_icon)

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
            mic_row = QHBoxLayout()
            mic_row.addWidget(microphone, 1)
            mic_row.addWidget(self.voice_activity)
            actions_card.content_layout.addLayout(mic_row)

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
            model.hide()

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
            self.metrics_panel.hide()

            fragment = Card(canvas, spacing=8)
            fragment.setObjectName("lastFragmentCard")
            self.fragment_title = QLabel("Текст диктовки", fragment)
            self.fragment_title.setProperty("sectionHeading", True)
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
            self.result_editor.setAccessibleName("Текст диктовки")
            self.result_editor.setMinimumHeight(160)
            self.result_editor.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self.result_editor.setReadOnly(True)
            self.result_editor.textChanged.connect(self._update_result_actions)
            fragment.content_layout.addWidget(self.result_editor, 1)
            self.new_text_label = QLabel("Новый текст", fragment)
            self.new_text_label.setProperty("muted", True)
            self.new_text_label.hide()
            fragment.content_layout.addWidget(self.new_text_label)
            self.unconfirmed_label = QLabel(fragment)
            self.unconfirmed_label.setTextFormat(Qt.TextFormat.PlainText)
            self.unconfirmed_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.unconfirmed_label.setWordWrap(True)
            self.unconfirmed_label.setProperty("muted", True)
            self.unconfirmed_label.hide()
            fragment.content_layout.addWidget(self.unconfirmed_label)
            self.result_editor.verticalScrollBar().valueChanged.connect(self._hide_new_text_at_end)
            result_actions = QHBoxLayout()
            self.result_copy = VoxButton("Копировать", fragment)
            self.result_copy_pending = VoxButton("Скопировать ожидающее", fragment)
            self.result_copy_pending.clicked.connect(self.copy_pending_requested)
            self.result_copy_pending.hide()
            self.result_edit = VoxButton("Править", fragment)
            self.result_save = VoxButton("Сохранить", fragment)
            self.result_save.clicked.connect(self.save_result_requested)
            self.result_edit.setEnabled(False)
            self.result_edit.clicked.connect(self._edit_result)
            self.result_insert = VoxButton("Вставить", fragment)
            self.result_clear = VoxButton("Очистить", fragment, variant="flat")
            self.result_target = VoxButton("Выбрать окно", fragment)
            self.result_clear.clicked.connect(self.clear_result_requested)
            self.result_target.clicked.connect(self.select_target_requested)
            self.result_copy.clicked.connect(self.copy_result_requested)
            self.result_insert.clicked.connect(self.insert_result_requested)
            for button in (
                self.result_copy,
                self.result_edit,
                self.result_save,
                self.result_insert,
                self.result_clear,
            ):
                result_actions.addWidget(button)
            result_actions.addStretch()
            fragment.content_layout.addLayout(result_actions)
            outer.addWidget(fragment, 1)
            delivery = QHBoxLayout()
            self.delivery_label = QLabel(actions_card)
            self.delivery_label.setWordWrap(True)
            self.delivery_label.setProperty("muted", True)
            delivery.addWidget(self.delivery_label, 1)
            delivery.addWidget(self.result_copy_pending)
            delivery.addWidget(self.result_target)
            actions_card.content_layout.addLayout(delivery)
            outer.addWidget(actions_card)
            outer.addWidget(self.metrics_panel)

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            dashboard = getattr(self, "_dashboard_scroll", None)
            if (
                not self._first_content_paint_seen
                and dashboard is not None
                and watched is dashboard.viewport()
                and event.type() is QEvent.Type.Paint
            ):
                self._first_content_paint_seen = True
                elapsed = time.perf_counter() - self._constructed_at
                self.first_paint_seconds = elapsed
                self.first_paint.emit(elapsed)
                canvas = dashboard.widget()
                _LOGGER.info(
                    "first_content_paint elapsed_ms=%.1f viewport=%dx%d canvas=%dx%d visible=%s",
                    elapsed * 1_000.0,
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
            if self.dictation_button.property("voiceMode") in {"starting", "processing"}:
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
                self._preview_start > 0
                if self._preview_start is not None
                else not self.result_editor.document().isEmpty()
            )
            active = self._state.state.active
            self.result_copy.setEnabled(available)
            self.result_insert.setEnabled(available and not active)
            self.result_save.setEnabled(available and not active)
            self.result_clear.setEnabled(available and not active)
            self.result_target.setEnabled(not active)

        def _edit_result(self) -> None:
            if self._state.state.active and self._live_document_session is not None:
                self._editing_live = not self._editing_live
                if self._editing_live:
                    edited = self.result_editor.document().isModified()
                    self._clear_preview()
                    self.result_editor.document().setModified(edited)
                    self.result_editor.setReadOnly(False)
                    self.result_editor.setFocus()
                # Restore the current hypothesis when leaving local edit mode.
                self._live_key = None
                self.update_snapshot(self._last_snapshot)
            else:
                self.edit_result_requested.emit()

        def result_text(self) -> str:
            if self._preview_start is None:
                return self.result_editor.toPlainText()
            cursor = QTextCursor(self.result_editor.document())
            cursor.setPosition(self._preview_start, QTextCursor.MoveMode.KeepAnchor)
            return cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n")

        def _clear_preview(self) -> None:
            if self._preview_start is None:
                return
            document = self.result_editor.document()
            position, self._preview_start = self._preview_start, None
            self._preview_text = ""
            if position <= document.characterCount() - 1:
                cursor = QTextCursor(document)
                cursor.setPosition(position)
                cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()

        def _add_preview(self, text: str) -> None:
            if not text:
                return
            cursor = QTextCursor(self.result_editor.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._preview_start = cursor.position()
            self._preview_text = text
            scheme = theme_manager(QApplication.instance())
            style = QTextCharFormat()
            style.setForeground(QColor(scheme.get_token("text_secondary")))
            cursor.insertText((" " if self._preview_start else "") + text, style)

        def set_live_text_source(self, source: Callable[[str, int, int], str]) -> None:
            self._live_source = source

        def set_live_presentation(self, *, show_provisional: bool, auto_scroll: bool) -> None:
            if self._show_live_provisional != show_provisional:
                self._live_key = None
            self._show_live_provisional = show_provisional
            if not show_provisional:
                self._clear_preview()
            self._live_auto_scroll = auto_scroll

        def _hide_new_text_at_end(self, position: int) -> None:
            if position == self.result_editor.verticalScrollBar().maximum():
                self.new_text_label.hide()

        def _update_result_editor(self, snapshot: object, state: UIState) -> None:
            unconfirmed = getattr(snapshot, "unconfirmed_tail", "")
            note = f"Неподтверждённый фрагмент\n{unconfirmed}" if unconfirmed else ""
            if note != self.unconfirmed_label.text():
                self.unconfirmed_label.setText(note)
                self.unconfirmed_label.setVisible(bool(note))
            session = getattr(snapshot, "live_session_id", None)
            active = state.state.active
            if active and session is not None and self._live_source is not None:
                if session != self._live_document_session:
                    self._clear_preview()
                    self.result_editor.clear()
                    self.result_editor.document().setModified(False)
                    self._live_document_session = session
                    self._live_cursor_offset = 0
                    self._live_key = None
                    self._editing_live = False
                live = getattr(snapshot, "live_transcript", None)
                key = (live.session_id, live.revision) if live is not None else None
                if key is None or key != self._live_key:
                    if key is None or self._live_key is None or key[1] > self._live_key[1]:
                        self._live_key = key
                        wanted = (
                            provisional_tail(snapshot)
                            if self._show_live_provisional and not self._editing_live
                            else ""
                        )
                    else:
                        wanted = self._preview_text
                else:
                    wanted = self._preview_text
                length = getattr(snapshot, "live_committed_length", 0)
                delta = (
                    self._live_source(session, self._live_cursor_offset, length)
                    if length > self._live_cursor_offset
                    else ""
                )
                if delta or wanted != self._preview_text:
                    scroll = self.result_editor.verticalScrollBar()
                    previous_position = scroll.value()
                    following = self._live_auto_scroll and previous_position == scroll.maximum()
                    document = self.result_editor.document()
                    edited = document.isModified()
                    self._clear_preview()
                    cursor = QTextCursor(document)
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    cursor.insertText(delta, QTextCharFormat())
                    self._live_cursor_offset += len(delta)
                    self._add_preview(wanted)
                    document.setModified(edited)
                    scroll.setValue(scroll.maximum() if following else previous_position)
                    self.new_text_label.setVisible(scroll.value() < scroll.maximum())
                self.result_editor.setReadOnly(not self._editing_live)
                return
            document = self.result_editor.document()
            edited = document.isModified()
            self._clear_preview()
            document.setModified(edited)
            finalized = state.full_transcript if not active else ""
            if self._live_document_session is not None:
                edited = self.result_editor.document().isModified()
                if active or (not edited and self.result_editor.toPlainText() != finalized):
                    self.result_editor.setPlainText(finalized)
                elif edited and getattr(snapshot, "live_final", None) is not None:
                    cursor = QTextCursor(self.result_editor.document())
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    cursor.insertText(finalized[self._live_cursor_offset :])
                self._live_document_session = None
                self._live_cursor_offset = 0
                self._editing_live = False
                self.new_text_label.hide()
            elif self._observed_result != finalized:
                self.result_editor.setPlainText(finalized)
            self._observed_result = finalized
            self.result_editor.setReadOnly(active)

        def set_profile_name(self, name: str) -> None:
            if self.shell.profile.text() == name:
                return
            self.session_profile_label.setText(name)
            self.shell.profile.setText(name)
            self.subtitle_label.setText(
                ("Профиль · " if self._interface_language == "ru" else "Profile · ") + name
            )

        def set_geometry_store(self, store: QSettings) -> None:
            self._geometry_store = store
            saved = store.value("mainWindow/geometry")
            if isinstance(saved, QByteArray):
                self.restoreGeometry(saved)
            self._geometry_timer = QTimer(self, interval=350, singleShot=True)
            self._geometry_timer.timeout.connect(self._save_geometry)

        def _save_geometry(self) -> None:
            if self._geometry_store is not None:
                self._geometry_store.setValue("mainWindow/geometry", self.saveGeometry())

        def moveEvent(self, event: QMoveEvent) -> None:
            super().moveEvent(event)
            if hasattr(self, "_geometry_timer"):
                self._geometry_timer.start()

        def resizeEvent(self, event: QResizeEvent) -> None:
            super().resizeEvent(event)
            if hasattr(self, "_geometry_timer"):
                self._geometry_timer.start()

        def _header_button(self, text: str, name: str) -> QPushButton:
            button = VoxButton(text, self)
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
            self.update_snapshot(getattr(self, "_last_snapshot", self._state))

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

        def update_snapshot(
            self,
            snapshot: UIState | object,
            presentation: DictationPresentation | None = None,
        ) -> None:
            state = snapshot_from(snapshot)
            self._last_snapshot = snapshot
            self._state = state
            display = presentation or DictationPresentationResolver.resolve(
                snapshot, self._interface_language
            )
            is_ru = self._interface_language == "ru"
            recognition = getattr(snapshot, "recognition_result", None)
            self.result_copy_pending.setVisible(
                bool(getattr(snapshot, "pending_insertion", False) and not state.state.active)
            )
            needs_model = bool(
                state.readiness is not None and state.readiness.reason == "model_missing"
            ) or (not self._model_available and not state.state.active)
            status_text = display.primary
            if needs_model and state.readiness is None:
                status_text = "Требуется модель" if is_ru else "Model required"
            self.status_dot.set_color(state_color(display.tone))
            self.status_label.setText(status_text)
            self.elapsed_label.setText(format_duration(display.duration_seconds))
            self.delivery_label.setText(display.delivery_label)
            self.status_detail_label.setText("")
            self.result_editor.setPlaceholderText(
                ("Говорите — текст появится здесь." if is_ru else "Speak to see text here.")
                if state.state.active
                else (
                    "Нажмите «Начать диктовку», чтобы говорить.\nТекст будет появляться здесь во время записи."
                    if is_ru
                    else "Select Start dictation to speak.\nYour words will appear here as you record."
                )
            )
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
            mic = display.microphone
            recording = display.capture is CaptureState.RECORDING
            self.audio_meter.set_active(recording and mic.capture_open)
            if recording and mic.capture_open:
                self.audio_meter.set_level(rms_to_meter(state.input_rms))
            processing = display.capture is CaptureState.STOPPING
            starting = display.capture is CaptureState.STARTING
            self.voice_activity.setCurrentIndex(int(starting or processing))
            self.processing.setAccessibleName(status_text)
            self.cancel_button.setVisible(bool(state.readiness and state.readiness.can_cancel))
            self.metrics_panel.setVisible(self.shell.current == "dictation")
            if mic.capture_open:
                signal_labels = {
                    "quiet": ("Тихий сигнал", "Quiet signal"),
                    "clipping": ("Слишком громко", "Clipping"),
                }
                signal = signal_labels.get(mic.signal_quality, ("", ""))[0 if is_ru else 1]
                detail = "Микрофон работает" if is_ru else "Microphone active"
                if signal:
                    detail = f"{detail} · {signal}"
                self.audio_hint.setText(detail)
                self.audio_hint.setToolTip(
                    f"{mic.signal_dbfs:.0f} dBFS" if mic.signal_dbfs is not None else ""
                )
            else:
                self.audio_hint.setText(
                    ("Открываем микрофон" if is_ru else "Opening microphone") if starting else ""
                )
                self.audio_hint.setToolTip("")
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
                else status_text
                if starting
                else ("Обрабатываем речь" if is_ru else "Processing speech")
                if processing
                else ui_text("stop" if active else "start", self._interface_language)
            )
            self.dictation_button.setText(self.voice_action_label.text())
            self.dictation_button.setAccessibleName(self.voice_action_label.text())
            self.dictation_button.setToolTip(self.voice_action_label.text())
            self.title_label.setText(
                ("Готов к диктовке" if is_ru else "Ready to dictate")
                if state.state is AppStatus.IDLE
                and not needs_model
                and (state.readiness is None or state.readiness.can_record)
                else status_text
            )
            self.shell.engine.hide()
            self.shell.model.setText(model_label)
            self._update_result_editor(snapshot, state)
            self._update_result_actions()
            self.result_edit.setText(
                ("Закончить правку" if is_ru else "Finish editing")
                if self._editing_live
                else ("Править" if is_ru else "Edit")
            )
            self.result_edit.setEnabled(
                self._live_document_session is not None
                or bool(recognition and recognition.raw_text and state.full_transcript)
            )
            mode = (
                "starting"
                if starting
                else "processing"
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
            failure = display.microphone.error_code
            detail = ""
            if failure:
                mic_messages = {
                    "microphone_missing": (
                        "Выбранный микрофон недоступен.",
                        "The selected microphone is unavailable.",
                    ),
                    "microphone_busy": (
                        "Микрофон занят другим приложением.",
                        "Another app is using the microphone.",
                    ),
                    "microphone_disconnected": (
                        "Микрофон отключён.",
                        "The microphone was disconnected.",
                    ),
                    "microphone_permission_denied": (
                        "Нет доступа к микрофону.",
                        "Microphone permission denied.",
                    ),
                    "microphone_open_failed": (
                        "Не удалось открыть микрофон.",
                        "Could not open the microphone.",
                    ),
                }
                detail = mic_messages.get(
                    failure, ("Не удалось проверить микрофон.", "Could not check the microphone.")
                )[0 if is_ru else 1]
            elif display.capture is CaptureState.FAILED:
                detail = state.error_message
            self.error_banner.setVisible(bool(detail))
            self.error_label.setText(detail)
            self.choose_microphone_button.setVisible(bool(failure))
            self.error_diagnostics_button.setVisible(not bool(failure))
            self.retry_button.setVisible(
                bool(failure or (state.readiness and state.readiness.can_retry))
            )
            self.copy_text_button.setVisible(display.has_confirmed)
            self.copy_text_button.setEnabled(display.has_confirmed)

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
            self.update_snapshot(getattr(self, "_last_snapshot", self._state))

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
            self.fragment_title.setText(
                "Текст диктовки" if locale == "ru" else "Dictation transcript"
            )
            self.hotkey_label.setText(getattr(self, "_dictation_mode_hint", self._toggle_hotkey))

        def set_toggle_hotkey(self, specification: str, mode_label: str = "") -> None:
            self._toggle_hotkey = specification
            self._dictation_mode_hint = " · ".join(filter(None, (mode_label, specification)))
            self.hotkey_label.setText(self._dictation_mode_hint)
            self.update_snapshot(getattr(self, "_last_snapshot", self._state))

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
