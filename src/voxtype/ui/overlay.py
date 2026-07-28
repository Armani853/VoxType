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
    from PySide6.QtGui import QCloseEvent, QMouseEvent, QShowEvent
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    from .theme import create_app_icon
    from .widgets import AudioLevelMeter, StatusDot, rms_to_meter

    _OVERLAY_STATES: dict[AppStatus, tuple[str, str]] = {
        AppStatus.STARTING: ("Запуск", "#657089"),
        AppStatus.MODEL_LOADING: ("Модель", "#7B7FF6"),
        AppStatus.IDLE: ("Готово", "#1A9B67"),
        AppStatus.LISTENING: ("Слушаю", "#5B5FEF"),
        AppStatus.SPEECH_DETECTED: ("Речь", "#1A9B67"),
        AppStatus.TRANSCRIBING: ("Обработка", "#7A55C5"),
        AppStatus.PAUSED_TARGET_LOST: ("Цель неактивна", "#D98B16"),
        AppStatus.FINISHING: ("Завершаю", "#7A55C5"),
        AppStatus.ERROR_RECOVERABLE: ("Внимание", "#D98B16"),
        AppStatus.ERROR_FATAL: ("Ошибка", "#D34A55"),
        AppStatus.SHUTTING_DOWN: ("Завершение", "#657089"),
        AppStatus.PAUSED: ("Пауза", "#D98B16"),
    }

    class DictationOverlay(QWidget):
        pause_requested = Signal()
        stop_requested = Signal()
        compact_changed = Signal(bool)
        position_changed = Signal(int, int)

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
            self.panel.setStyleSheet(
                "QFrame#overlayPanel { background: rgba(24, 29, 40, 247); "
                "border: 1px solid rgba(255,255,255,34); border-radius: 16px; } "
                "QLabel { color: #F7F8FB; font-family: 'Segoe UI Variable', 'Segoe UI'; } "
                "QLabel[muted='true'] { color: #AEB6C8; } "
                "QPushButton { color: #F7F8FB; background: #30384A; border: none; "
                "border-radius: 8px; padding: 5px 10px; min-height: 25px; } "
                "QPushButton:hover { background: #414C62; } "
                "QPushButton[danger='true'] { background: #C94652; }"
            )
            shadow = QGraphicsDropShadowEffect(self.panel)
            shadow.setBlurRadius(30)
            shadow.setOffset(0, 6)
            shadow.setColor(Qt.GlobalColor.black)
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
            self.status_label.setStyleSheet("font-weight: 650;")
            self.processing_label = QLabel("Локально", self.panel)
            self.processing_label.setProperty("muted", True)
            self.processing_label.setStyleSheet("font-size: 8pt;")
            status_column.addWidget(self.status_label)
            status_column.addWidget(self.processing_label)
            compact_row.addLayout(status_column)
            self.language_label = QLabel("RU", self.panel)
            self.language_label.setObjectName("overlayLanguageLabel")
            self.language_label.setStyleSheet(
                "color: #AEB0FF; background: #30335C; border-radius: 6px; "
                "font-weight: 700; padding: 3px 6px;"
            )
            compact_row.addWidget(self.language_label)
            self.timer_label = QLabel("00:00:00", self.panel)
            self.timer_label.setObjectName("overlayTimerLabel")
            self.timer_label.setStyleSheet(
                "font-family: 'Cascadia Mono', Consolas; font-weight: 600;"
            )
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
            metadata = QHBoxLayout()
            self.queue_label = QLabel("Очередь: 0.0 с", self.expanded_panel)
            self.queue_label.setObjectName("overlayQueueLabel")
            self.queue_label.setProperty("muted", True)
            self.target_label = QLabel("Цель: —", self.expanded_panel)
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
            panel_layout.addWidget(self.expanded_panel)
            outer.addWidget(self.panel)

        def _connect_ui(self) -> None:
            self.expand_button.clicked.connect(self.toggle_compact)
            self.pause_button.clicked.connect(self.pause_requested)
            self.stop_button.clicked.connect(self.stop_requested)

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
            text, color = _OVERLAY_STATES[state.state]
            self.status_label.setText(text)
            self.status_dot.set_color(color)
            self.language_label.setText(state.language.value.upper())
            self.timer_label.setText(format_duration(state.session_elapsed_s))
            self.audio_meter.set_level(rms_to_meter(state.input_rms), state.clipping_percentage)
            self.processing_label.setText(self._processing_text(state))
            transcript = state.provisional_text or state.last_committed_text
            self.provisional_label.setText(text_preview(transcript, 420) or "Ожидаю речь…")
            self.queue_label.setText(f"Очередь: {format_backlog(state.queued_audio_seconds)}")
            target = state.target_title or "не выбрана"
            self.target_label.setText(f"Цель: {target}")
            self.target_label.setToolTip(target)
            paused = state.state in {AppStatus.PAUSED, AppStatus.PAUSED_TARGET_LOST}
            self.pause_button.setText("Продолжить" if paused else "Пауза")
            self.stop_button.setEnabled(state.state.active)

        @staticmethod
        def _processing_text(state: UIState) -> str:
            if state.error_message:
                return state.error_message
            if state.queued_audio_seconds >= 10:
                return "Обработка отстаёт"
            if state.clipping_percentage > 0.1 or state.input_rms >= 0.98:
                return "Слишком громко"
            if state.state is AppStatus.PAUSED_TARGET_LOST:
                return "Текст сохранён в очереди"
            return "LOCAL"

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
            self.setMaximumHeight(96 if self._compact else 260)
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
