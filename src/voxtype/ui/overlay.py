"""Non-activating, draggable dictation overlay for Windows."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .models import (
    AppStatus,
    UIState,
    format_backlog,
    format_duration,
    snapshot_from,
    text_preview,
)

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QPoint, QSettings, Qt, Signal
    from PySide6.QtGui import QCloseEvent, QColor, QKeyEvent, QMouseEvent, QShowEvent
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    from .theme import OVERLAY_PALETTE, create_app_icon, overlay_stylesheet
    from .widgets import AudioLevelMeter, ElidedLabel, StatusDot, rms_to_meter

    _OVERLAY_STATES: dict[AppStatus, tuple[str, str]] = {
        AppStatus.STARTING: ("Запуск", "text_tertiary"),
        AppStatus.MODEL_LOADING: ("Модель", "primary"),
        AppStatus.IDLE: ("Готово", "success"),
        AppStatus.LISTENING: ("Слушаю", "primary"),
        AppStatus.SPEECH_DETECTED: ("Речь", "success"),
        AppStatus.TRANSCRIBING: ("Обработка", "primary"),
        AppStatus.PAUSED_TARGET_LOST: ("Цель неактивна", "warning"),
        AppStatus.FINISHING: ("Завершаю", "primary"),
        AppStatus.COMPLETED: ("Готово", "success"),
        AppStatus.ERROR_RECOVERABLE: ("Внимание", "warning"),
        AppStatus.ERROR_FATAL: ("Ошибка", "error"),
        AppStatus.SHUTTING_DOWN: ("Завершение", "text_tertiary"),
        AppStatus.PAUSED: ("Пауза", "warning"),
    }

    class DictationOverlay(QWidget):
        pause_requested = Signal()
        stop_requested = Signal()
        compact_changed = Signal(bool)
        position_changed = Signal(int, int)
        copy_all_requested = Signal()
        insert_requested = Signal()
        close_result_requested = Signal()

        def __init__(
            self,
            controller: object | None = None,
            parent: QWidget | None = None,
            *,
            settings: QSettings | None = None,
        ) -> None:
            flags = (
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            super().__init__(parent, flags)
            self.setObjectName("dictationOverlay")
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setWindowTitle("VoxType")
            self.setWindowIcon(create_app_icon())
            self._settings = settings or QSettings("VoxType", "VoxType")
            self._drag_offset: QPoint | None = None
            self._state = UIState()
            self._compact = bool(self._settings.value("ui/overlayCompact", True, type=bool))
            self._controller_connections: list[tuple[Any, Callable[..., Any]]] = []
            self._build_ui()
            self._connect_ui()
            self.set_compact(self._compact, persist=False)
            self._restore_position()
            if controller is not None:
                self.bind_controller(controller)

        def _build_ui(self) -> None:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(12, 12, 12, 12)
            self.panel = QFrame(self)
            self.panel.setObjectName("overlayPanel")
            self.panel.setStyleSheet(overlay_stylesheet())
            shadow = QGraphicsDropShadowEffect(self.panel)
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(0, 0, 0, 90))
            self.panel.setGraphicsEffect(shadow)
            panel_layout = QVBoxLayout(self.panel)
            panel_layout.setContentsMargins(14, 10, 12, 11)
            panel_layout.setSpacing(9)

            compact_row = QHBoxLayout()
            compact_row.setSpacing(9)
            icon = QLabel(self.panel)
            icon.setPixmap(create_app_icon(36).pixmap(32, 32))
            icon.setFixedSize(32, 32)
            compact_row.addWidget(icon)
            self.status_dot = StatusDot(self.panel)
            compact_row.addWidget(self.status_dot)
            status_column = QVBoxLayout()
            status_column.setSpacing(0)
            self.status_label = QLabel("Готово", self.panel)
            self.status_label.setObjectName("overlayStatusLabel")
            self.status_label.setProperty("overlayStatus", True)
            self.processing_label = QLabel("Локально", self.panel)
            self.processing_label.setProperty("overlayCaption", True)
            status_column.addWidget(self.status_label)
            status_column.addWidget(self.processing_label)
            compact_row.addLayout(status_column)
            self.language_label = QLabel("RU", self.panel)
            self.language_label.setObjectName("overlayLanguageLabel")
            self.language_label.setProperty("overlayLanguage", True)
            compact_row.addWidget(self.language_label)
            self.timer_label = QLabel("00:00:00", self.panel)
            self.timer_label.setObjectName("overlayTimerLabel")
            compact_row.addWidget(self.timer_label)
            self.audio_meter = AudioLevelMeter(self.panel)
            self.audio_meter.setObjectName("overlayAudioMeter")
            self.audio_meter.setFixedWidth(82)
            compact_row.addWidget(self.audio_meter)
            self.expand_button = QPushButton("⌄", self.panel)
            self.expand_button.setObjectName("overlayExpandButton")
            self.expand_button.setToolTip("Развернуть")
            self.expand_button.setAccessibleName("Развернуть индикатор")
            self.expand_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.expand_button.setFixedSize(28, 28)
            compact_row.addWidget(self.expand_button)
            panel_layout.addLayout(compact_row)

            self.expanded_panel = QWidget(self.panel)
            expanded_layout = QVBoxLayout(self.expanded_panel)
            expanded_layout.setContentsMargins(0, 3, 0, 0)
            expanded_layout.setSpacing(8)
            self.provisional_label = QLabel("Ожидаю речь…", self.expanded_panel)
            self.provisional_label.setObjectName("overlayProvisionalLabel")
            self.provisional_label.setWordWrap(True)
            self.provisional_label.setMinimumHeight(38)
            self.provisional_label.setMaximumHeight(68)
            self.provisional_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            expanded_layout.addWidget(self.provisional_label)
            self.result_text = QPlainTextEdit(self.expanded_panel)
            self.result_text.setObjectName("overlayResultText")
            self.result_text.setReadOnly(True)
            self.result_text.setMinimumHeight(110)
            self.result_text.setMaximumHeight(150)
            self.result_text.hide()
            expanded_layout.addWidget(self.result_text)
            metadata = QHBoxLayout()
            self.queue_label = QLabel("Очередь: 0.0 с", self.expanded_panel)
            self.queue_label.setObjectName("overlayQueueLabel")
            self.queue_label.setProperty("muted", True)
            self.target_label = ElidedLabel("Цель: —", self.expanded_panel)
            self.target_label.setObjectName("overlayTargetLabel")
            self.target_label.setProperty("muted", True)
            self.target_label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            metadata.addWidget(self.queue_label)
            metadata.addWidget(self.target_label, 1)
            expanded_layout.addLayout(metadata)
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            self.pause_button = QPushButton("Пауза", self.expanded_panel)
            self.pause_button.setObjectName("overlayPauseButton")
            self.pause_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.stop_button = QPushButton("Остановить", self.expanded_panel)
            self.stop_button.setObjectName("overlayStopButton")
            self.stop_button.setProperty("danger", True)
            self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            buttons.addWidget(self.pause_button)
            buttons.addWidget(self.stop_button)
            expanded_layout.addLayout(buttons)
            self.result_buttons = QWidget(self.expanded_panel)
            result_layout = QHBoxLayout(self.result_buttons)
            result_layout.setContentsMargins(0, 0, 0, 0)
            result_layout.addStretch(1)
            self.copy_all_button = QPushButton("Копировать всё", self.result_buttons)
            self.insert_button = QPushButton("Вставить / повторить", self.result_buttons)
            self.close_result_button = QPushButton("Закрыть", self.result_buttons)
            self.close_result_button.setProperty("flat", True)
            result_layout.addWidget(self.copy_all_button)
            result_layout.addWidget(self.insert_button)
            result_layout.addWidget(self.close_result_button)
            self.result_buttons.hide()
            expanded_layout.addWidget(self.result_buttons)
            panel_layout.addWidget(self.expanded_panel)
            outer.addWidget(self.panel)

        def _connect_ui(self) -> None:
            self.expand_button.clicked.connect(self.toggle_compact)
            self.pause_button.clicked.connect(self.pause_requested)
            self.stop_button.clicked.connect(self.stop_requested)
            self.copy_all_button.clicked.connect(self.copy_all_requested)
            self.insert_button.clicked.connect(self.insert_requested)
            self.close_result_button.clicked.connect(self.close_result_requested)

        def bind_controller(self, controller: object) -> None:
            self.unbind_controller()
            for signal, method_name in (
                (self.pause_requested, "toggle_pause"),
                (self.stop_requested, "toggle_dictation"),
            ):
                method = getattr(controller, method_name, None)
                if callable(method):
                    signal.connect(method)
                    self._controller_connections.append((signal, method))

        def unbind_controller(self) -> None:
            for signal, callback in self._controller_connections:
                try:
                    signal.disconnect(callback)
                except (RuntimeError, TypeError):
                    continue
            self._controller_connections.clear()

        def update_snapshot(self, snapshot: UIState | object) -> None:
            state = snapshot_from(snapshot)
            self._state = state
            text, color_role = _OVERLAY_STATES[state.state]
            self.status_label.setText(text)
            self.status_dot.set_color(OVERLAY_PALETTE[color_role])
            self.language_label.setText(state.language.value.upper())
            self.timer_label.setText(format_duration(state.session_elapsed_s))
            self.audio_meter.set_level(rms_to_meter(state.input_rms), state.clipping_percentage)
            self.processing_label.setText(self._processing_text(state))
            self.processing_label.setToolTip(
                "Автоматическая вставка не выполнена; полный текст сохранён."
                if state.state is AppStatus.COMPLETED
                and state.delivery_status in {"failed", "queued_target_inactive", "unknown"}
                else ""
            )
            transcript = (
                state.full_transcript
                if state.state is AppStatus.COMPLETED
                else state.provisional_text or state.last_committed_text
            )
            completed = state.state is AppStatus.COMPLETED
            self.provisional_label.setVisible(not completed)
            self.result_text.setVisible(completed)
            if completed:
                self.result_text.setPlainText(transcript)
            else:
                self.provisional_label.setText(text_preview(transcript, 420) or "Ожидаю речь…")
            self.queue_label.setText(f"Очередь: {format_backlog(state.queued_audio_seconds)}")
            self.queue_label.setVisible(not completed)
            target = state.target_title or "не выбрана"
            self.target_label.setText(f"Цель: {target}")
            self.target_label.setToolTip(target)
            paused = state.state in {AppStatus.PAUSED, AppStatus.PAUSED_TARGET_LOST}
            self.pause_button.setText("Продолжить" if paused else "Пауза")
            self.stop_button.setEnabled(state.state.active)
            self.pause_button.setVisible(not completed)
            self.stop_button.setVisible(not completed)
            self.result_buttons.setVisible(completed)
            self.insert_button.setEnabled(
                state.delivery_status in {"pending", "failed", "queued_target_inactive"}
            )
            insertion_failed = state.delivery_status in {
                "failed",
                "queued_target_inactive",
                "unknown",
            }
            self.copy_all_button.setProperty("primary", completed and insertion_failed)
            self._refresh_dynamic_style(self.copy_all_button)
            if completed and self._compact:
                self.set_compact(False, persist=False)

        @staticmethod
        def _processing_text(state: UIState) -> str:
            if state.error_message:
                return state.error_message
            if state.state is AppStatus.COMPLETED:
                if state.delivery_status == "delivered":
                    return "Текст вставлен"
                if state.delivery_status in {"failed", "queued_target_inactive", "unknown"}:
                    return "Текст сохранён"
                return "Полный текст сохранён"
            if state.queued_audio_seconds >= 10:
                return "Обработка отстаёт"
            if state.clipping_percentage > 0.1 or state.input_rms >= 0.98:
                return "Слишком громко"
            if state.state is AppStatus.PAUSED_TARGET_LOST:
                return "Текст сохранён в очереди"
            return "LOCAL"

        @staticmethod
        def _refresh_dynamic_style(widget: QWidget) -> None:
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

        def toggle_compact(self) -> None:
            self.set_compact(not self._compact)

        def set_compact(self, compact: bool, *, persist: bool = True) -> None:
            self._compact = bool(compact)
            self.expanded_panel.setVisible(not self._compact)
            self.expand_button.setText("⌄" if self._compact else "⌃")
            self.expand_button.setToolTip("Развернуть" if self._compact else "Свернуть")
            self.expand_button.setAccessibleName(
                "Развернуть индикатор" if self._compact else "Свернуть индикатор"
            )
            self.setFixedWidth(410 if self._compact else 480)
            self.setMinimumHeight(70 if self._compact else 180)
            self.setMaximumHeight(96 if self._compact else 380)
            self.adjustSize()
            if persist:
                self._settings.setValue("ui/overlayCompact", self._compact)
                self._settings.sync()
            self.compact_changed.emit(self._compact)

        def is_compact(self) -> bool:
            return self._compact

        def show_without_activating(self) -> None:
            self.show()
            self._apply_windows_no_activate()

        def _restore_position(self) -> None:
            saved_x = self._settings.value("ui/overlayX", None)
            saved_y = self._settings.value("ui/overlayY", None)
            if saved_x is None or saved_y is None:
                screen = QApplication.primaryScreen()
                if screen is not None:
                    area = screen.availableGeometry()
                    self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
                return
            try:
                point = QPoint(int(str(saved_x)), int(str(saved_y)))
            except (TypeError, ValueError):
                return
            self.move(self._clamped_position(point))

        def _clamped_position(self, point: QPoint) -> QPoint:
            screen = QApplication.screenAt(point) or QApplication.primaryScreen()
            if screen is None:
                return point
            area = screen.availableGeometry()
            visible_margin = 48
            x = min(area.right() - visible_margin, max(area.left(), point.x()))
            y = min(area.bottom() - visible_margin, max(area.top(), point.y()))
            return QPoint(x, y)

        def _save_position(self) -> None:
            position = self.pos()
            self._settings.setValue("ui/overlayX", position.x())
            self._settings.setValue("ui/overlayY", position.y())
            self._settings.setValue("ui/overlayCompact", self._compact)
            self._settings.sync()
            self.position_changed.emit(position.x(), position.y())

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() is Qt.MouseButton.LeftButton:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
                target = event.globalPosition().toPoint() - self._drag_offset
                self.move(self._clamped_position(target))
                event.accept()
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if event.button() is Qt.MouseButton.LeftButton and self._drag_offset is not None:
                self._drag_offset = None
                self._save_position()
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def showEvent(self, event: QShowEvent) -> None:
            super().showEvent(event)
            self._apply_windows_no_activate()

        def keyPressEvent(self, event: QKeyEvent) -> None:
            if event.key() == Qt.Key.Key_Escape and self._state.state is AppStatus.COMPLETED:
                self.close_result_requested.emit()
                event.accept()
                return
            super().keyPressEvent(event)

        def closeEvent(self, event: QCloseEvent) -> None:
            self._save_position()
            self.unbind_controller()
            event.accept()

        def _apply_windows_no_activate(self) -> None:
            """Apply Win32 styles that keep the overlay out of Alt+Tab and focus."""

            if sys.platform != "win32":
                return
            import ctypes
            from ctypes import wintypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            get_window_long = user32.GetWindowLongPtrW
            set_window_long = user32.SetWindowLongPtrW
            get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
            get_window_long.restype = ctypes.c_ssize_t
            set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
            set_window_long.restype = ctypes.c_ssize_t
            ex_style_index = -20
            ws_ex_toolwindow = 0x00000080
            ws_ex_noactivate = 0x08000000
            style = int(get_window_long(hwnd, ex_style_index))
            set_window_long(
                hwnd,
                ex_style_index,
                style | ws_ex_toolwindow | ws_ex_noactivate,
            )
            hwnd_topmost = -1
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_noactivate = 0x0010
            user32.SetWindowPos.argtypes = (
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            )
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.SetWindowPos(
                hwnd,
                wintypes.HWND(hwnd_topmost),
                0,
                0,
                0,
                0,
                swp_nosize | swp_nomove | swp_noactivate,
            )
            sw_shownoactivate = 4
            user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.ShowWindow.restype = wintypes.BOOL
            user32.ShowWindow(hwnd, sw_shownoactivate)

else:

    class DictationOverlay:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
