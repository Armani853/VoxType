"""Non-activating, draggable dictation overlay for Windows."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .models import (
    AppStatus,
    UIState,
    format_duration,
    snapshot_from,
    text_preview,
)

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QPoint, QSettings, Qt, Signal
    from PySide6.QtGui import QCloseEvent, QKeyEvent, QMouseEvent, QScreen, QShowEvent
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from .motion import AnimationCoordinator
    from .obsidian import AnimatedStatusDot, IconProvider, WaveformWidget
    from .theme import OVERLAY_PALETTE, create_app_icon, overlay_stylesheet
    from .widgets import ElidedLabel, rms_to_meter

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
        cancel_processing_requested = Signal()
        compact_changed = Signal(bool)
        position_changed = Signal(int, int)
        copy_all_requested = Signal()
        insert_requested = Signal()
        close_result_requested = Signal()
        hide_requested = Signal()
        close_overlay_requested = Signal()
        open_buffer_requested = Signal()

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
            self._composited = self._has_composition()
            self._hiding = False
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, self._composited)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setWindowTitle("VoxType")
            self.setWindowIcon(create_app_icon())
            self._settings = settings if settings is not None else QSettings("VoxType", "VoxType")
            self._drag_offset: QPoint | None = None
            self._state = UIState()
            self._compact = bool(self._settings.value("ui/overlayCompact", True, type=bool))
            stored_mode = str(
                self._settings.value("ui/overlayMode", "standard" if self._compact else "expanded")
            )
            self._preferred_mode = (
                stored_mode if stored_mode in ("mini", "standard", "expanded") else "standard"
            )
            self._mode = ""
            self._preview = ""
            self._preview_changed = ("", -1)
            self._hypothesis = ""
            self._show_provisional = True
            self._controller_connections: list[tuple[Any, Callable[..., Any]]] = []
            self._build_ui()
            self._connect_ui()
            self.set_mode(self._preferred_mode, persist=False)
            self._restore_position()
            if controller is not None:
                self.bind_controller(controller)

        def _build_ui(self) -> None:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            self.panel = QFrame(self)
            self.panel.setObjectName("overlayPanel")
            self.panel.setStyleSheet(overlay_stylesheet(self._composited))
            content = QVBoxLayout(self.panel)
            content.setContentsMargins(10, 5, 10, 5)
            content.setSpacing(2)
            line = QHBoxLayout()
            line.setSpacing(5)
            self.status_dot = AnimatedStatusDot(self.panel)
            self.status_label = ElidedLabel("Готово", self.panel)
            self.status_label.setProperty("overlayStatus", True)
            self.status_label.setObjectName("overlayStatusLabel")
            self.processing_label = ElidedLabel("", self.panel)
            self.processing_label.setProperty("overlayCaption", True)
            self.language_label = QLabel("RU", self.panel)
            self.language_label.setObjectName("overlayLanguageLabel")
            self.timer_label = QLabel("", self.panel)
            self.timer_label.setObjectName("overlayTimerLabel")
            self.mini_wave = WaveformWidget(self.panel, bars=13, height=24)
            self.stop_button = self._overlay_button("stop", "Остановить")
            self.expand_button = self._overlay_button("expand", "Развернуть буфер")
            self.minimize_button = self._overlay_button("minimize", "Свернуть в трей")
            self.hide_button = self._overlay_button("close", "Скрыть окно")
            for widget in (
                self.status_dot,
                self.status_label,
                self.mini_wave,
                self.language_label,
                self.timer_label,
                self.stop_button,
                self.minimize_button,
                self.expand_button,
                self.hide_button,
            ):
                line.addWidget(widget, 1 if widget is self.status_label else 0)
            content.addLayout(line)
            self.audio_meter = WaveformWidget(self.panel, bars=25, height=24)
            middle = QHBoxLayout()
            middle.setSpacing(5)
            middle.addWidget(self.audio_meter)
            middle.addWidget(self.processing_label, 1)
            content.addLayout(middle)
            self.expanded_panel = QWidget(self.panel)
            details = QHBoxLayout(self.expanded_panel)
            details.setContentsMargins(0, 0, 0, 0)
            details.setSpacing(2)
            self.provisional_label = ElidedLabel("", self.expanded_panel)
            self.provisional_label.setObjectName("overlayProvisionalLabel")
            details.addWidget(self.provisional_label, 1)
            self.result_text = QPlainTextEdit(self.expanded_panel)
            self.result_text.setObjectName("overlayResultText")
            self.result_text.setReadOnly(True)
            self.result_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.result_text.hide()
            self.queue_label = QLabel(self.expanded_panel)
            self.queue_label.hide()
            self.target_label = ElidedLabel("", self.expanded_panel)
            self.target_label.hide()
            self.pause_button = self._overlay_button("stop", "Пауза / продолжить")
            details.addWidget(self.pause_button)
            self.result_buttons = QWidget(self.expanded_panel)
            actions = QHBoxLayout(self.result_buttons)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(2)
            self.copy_all_button = self._overlay_button("snippets", "Копировать результат")
            self.insert_button = self._overlay_button("check", "Повторить вставку")
            self.open_buffer_button = self._overlay_button("files", "Открыть буфер")
            self.start_button = self._overlay_button("mic", "Начать новую диктовку")
            for button in (
                self.copy_all_button,
                self.insert_button,
                self.open_buffer_button,
                self.start_button,
            ):
                actions.addWidget(button)
            details.addWidget(self.result_buttons)
            content.addWidget(self.expanded_panel)
            content.addWidget(self.result_text, 1)
            outer.addWidget(self.panel)

        def _overlay_button(self, icon: str, label: str) -> QPushButton:
            button = QPushButton(self.panel)
            button.setProperty("overlayIcon", True)
            button.setIcon(
                IconProvider._pixmap(icon, "dark", 16, button.devicePixelRatioF(), "#F2F3F8")
            )
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(24, 24)
            return button

        def _connect_ui(self) -> None:
            self.expand_button.clicked.connect(self.toggle_compact)
            self.minimize_button.clicked.connect(self.minimize_panel)
            self.hide_button.clicked.connect(self.close_overlay_requested)
            self.open_buffer_button.clicked.connect(self.open_buffer_requested)
            self.start_button.clicked.connect(self.stop_requested)
            self.pause_button.clicked.connect(self.pause_requested)
            self.stop_button.clicked.connect(self._request_stop)
            self.copy_all_button.clicked.connect(self.copy_all_requested)
            self.insert_button.clicked.connect(self.insert_requested)

        def hide_for_user(self) -> None:
            self._save_position()
            self.hide()
            self.hide_requested.emit()

        def set_show_provisional(self, enabled: bool) -> None:
            self._show_provisional = enabled

        def _request_stop(self) -> None:
            if self._state.state is AppStatus.FINISHING:
                self.cancel_processing_requested.emit()
            else:
                self.stop_requested.emit()

        def bind_controller(self, controller: object) -> None:
            self.unbind_controller()
            for signal, method_name in (
                (self.pause_requested, "toggle_pause"),
                (self.stop_requested, "toggle_dictation"),
                (self.cancel_processing_requested, "cancel_processing"),
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
            previous = self._state
            self._state = state
            text, color_role = _OVERLAY_STATES[state.state]
            if state.readiness is not None and not state.state.active:
                text = state.readiness.message
            completed = state.state is AppStatus.COMPLETED
            if completed and state.delivery_status == "queued_target_inactive":
                text, color_role = "Ожидает вставки", "warning"
            elif completed and state.delivery_status == "no_result":
                text, color_role = "Нет результата", "warning"
            self.status_label.setText(text)
            self.status_dot.set_color(OVERLAY_PALETTE[color_role])
            self.language_label.setText(state.language.value.upper())
            self.timer_label.setText(format_duration(state.session_elapsed_s))
            recording = state.state in {
                AppStatus.LISTENING,
                AppStatus.SPEECH_DETECTED,
                AppStatus.TRANSCRIBING,
            }
            for meter in (self.audio_meter, self.mini_wave):
                meter.set_active(recording)
                if not meter.isHidden() and recording:
                    meter.set_level(rms_to_meter(state.input_rms))
            self.processing_label.setText(self._processing_text(state))
            self.processing_label.setToolTip(self._processing_text(state))
            live = getattr(snapshot, "live_transcript", None) if self._show_provisional else None
            key = (live.session_id, live.revision) if live is not None else ("", -1)
            if key != self._preview_changed:
                self._preview_changed = key
                self._hypothesis = (
                    " ".join(
                        (*[part.text for part in live.stable_segments], live.unstable_text)
                    ).strip()
                    if live is not None
                    else ""
                )
            transcript = (
                state.full_transcript
                if completed
                else (self._hypothesis or state.last_committed_text or state.provisional_text)
            )
            preview = text_preview(transcript, 700)
            if preview != self._preview:
                self._preview = preview
                self.provisional_label.setText(text_preview(preview, 160))
                self.provisional_label.setToolTip(text_preview(preview, 420))
                if self._mode != "mini":
                    self.result_text.setPlainText(preview)
            finishing = state.state is AppStatus.FINISHING
            self.stop_button.setToolTip("Отменить обработку" if finishing else "Остановить")
            self.stop_button.setAccessibleName(self.stop_button.toolTip())
            self.stop_button.setVisible((state.state.active or finishing) and not completed)
            self.pause_button.setVisible(state.state.active and not finishing)
            self.pause_button.setToolTip(
                "Продолжить"
                if state.state in {AppStatus.PAUSED, AppStatus.PAUSED_TARGET_LOST}
                else "Пауза"
            )
            self.result_buttons.setVisible(completed or state.state is AppStatus.IDLE)
            self.copy_all_button.setEnabled(bool(transcript))
            self.insert_button.setEnabled(
                state.delivery_status in {"failed", "queued_target_inactive"}
            )
            if completed and previous.state is not AppStatus.COMPLETED:
                self.set_mode("expanded", persist=False)
            elif not completed and previous.state is AppStatus.COMPLETED:
                self.set_mode(self._preferred_mode, persist=False)

        @staticmethod
        def _processing_text(state: UIState) -> str:
            if state.error_message:
                return state.error_message
            if state.state is AppStatus.COMPLETED:
                if state.delivery_status == "no_result":
                    return state.status_message
                if state.delivery_status in {"delivered", "submitted_unconfirmed"}:
                    return "Команда вставки отправлена; проверьте целевое поле"
                if state.delivery_status == "copied":
                    return "Текст скопирован"
                if state.delivery_status == "queued_target_inactive":
                    return "Вернитесь в целевое окно"
                if state.delivery_status in {"failed", "unknown", "copy_required"}:
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
            self.set_mode("expanded" if self._mode == "standard" else "standard")

        def minimize_panel(self) -> None:
            self.set_mode("mini")
            self.hide_for_user()

        def set_compact(self, compact: bool, *, persist: bool = True) -> None:
            self.set_mode("standard" if compact else "expanded", persist=persist)

        def set_mode(self, mode: str, *, persist: bool = True) -> None:
            self._mode = mode
            self._compact = mode != "expanded"
            self.mini_wave.setVisible(mode == "mini")
            self.status_label.show()
            self.language_label.setVisible(mode != "mini")
            self.timer_label.setVisible(mode == "expanded")
            self.audio_meter.setVisible(mode != "mini")
            self.processing_label.setVisible(mode != "mini")
            self.stop_button.setVisible(self._state.state.active)
            self.result_buttons.setVisible(
                self._state.state in {AppStatus.IDLE, AppStatus.COMPLETED}
            )
            self.pause_button.setVisible(self._state.state.active)
            self.expanded_panel.setVisible(mode != "mini")
            self.result_text.setVisible(mode != "mini")
            if mode != "mini":
                self.result_text.setPlainText(self._preview)
            self._resize_to_screen()
            if persist:
                self._preferred_mode = mode
                self._settings.setValue("ui/overlayMode", mode)
                self._settings.setValue("ui/overlayCompact", self._compact)
            self._restore_position()
            self.compact_changed.emit(self._compact)

        def is_compact(self) -> bool:
            return self._compact

        def _resize_to_screen(self) -> None:
            width, height = {"mini": (300, 64), "standard": (420, 180), "expanded": (520, 330)}[
                self._mode
            ]
            screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                width, height = min(width, area.width()), min(height, area.height())
            self.setFixedSize(width, height)

        def place_near_caret(self, rect: tuple[int, int, int, int] | None) -> None:
            if self._drag_offset is not None:
                return
            if rect is None:
                self._restore_position()
                return
            geometry = self._logical_caret_geometry(rect)
            if geometry is None:
                self._restore_position()
                return
            screen, left, top, bottom = geometry
            area = screen.availableGeometry()
            if area.width() < self.width() or area.height() < self.height():
                self._restore_position()
                return
            y = top - self.height() - 14
            if y < area.top():
                y = bottom + 14
            if y + self.height() > area.bottom() + 1:
                self._restore_position()
                return
            x = max(area.left(), min(left, area.right() - self.width() + 1))
            self.move(x, y)

        @staticmethod
        def _logical_caret_geometry(
            rect: tuple[int, int, int, int],
        ) -> tuple[QScreen, int, int, int] | None:
            left, top, right, bottom = rect
            if right < left or bottom < top:
                return None
            if sys.platform != "win32":
                screen = QApplication.screenAt(QPoint(left, top))
                return (screen, left, top, bottom) if screen is not None else None
            try:
                from ctypes import wintypes

                class MonitorInfo(ctypes.Structure):
                    _fields_ = (
                        ("size", wintypes.DWORD),
                        ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT),
                        ("flags", wintypes.DWORD),
                        ("device", wintypes.WCHAR * 32),
                    )

                user32 = ctypes.windll.user32
                monitor_at = user32.MonitorFromPoint
                monitor_at.argtypes = (wintypes.POINT, wintypes.DWORD)
                monitor_at.restype = wintypes.HANDLE
                monitor_info = user32.GetMonitorInfoW
                monitor_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
                monitor_info.restype = wintypes.BOOL
                handle = monitor_at(wintypes.POINT(left, top), 0)
                info = MonitorInfo()
                info.size = ctypes.sizeof(info)
                if not handle or not monitor_info(handle, ctypes.byref(info)):
                    return None
                screen = next(
                    (
                        item
                        for item in QApplication.screens()
                        if item.name().casefold() == info.device.casefold()
                    ),
                    None,
                )
                if screen is None or (scale := screen.devicePixelRatio()) <= 0:
                    return None
                origin = screen.geometry().topLeft()
                return (
                    screen,
                    origin.x() + round((left - info.monitor.left) / scale),
                    origin.y() + round((top - info.monitor.top) / scale),
                    origin.y() + round((bottom - info.monitor.top) / scale),
                )
            except (AttributeError, OSError, OverflowError):
                return None

        def show_without_activating(self) -> None:
            if not self.isVisible() or self._hiding:
                self._hiding = False
                self.show()
                motion = AnimationCoordinator.instance()
                motion.animate_opacity(self.panel, 0.0, 1.0, motion.OVERLAY_ENTER)

        def hide(self) -> None:
            if not self.isVisible() or self._hiding:
                return
            self._hiding = True
            motion = AnimationCoordinator.instance()
            motion.animate_opacity(
                self.panel, 1.0, 0.0, motion.OVERLAY_EXIT, finished=self._finish_hide
            )

        def _finish_hide(self) -> None:
            if self._hiding:
                self._hiding = False
                super().hide()

        @staticmethod
        def _has_composition() -> bool:
            if sys.platform != "win32":
                return False
            enabled = ctypes.c_int()
            try:
                return ctypes.windll.dwmapi.DwmIsCompositionEnabled(
                    ctypes.byref(enabled)
                ) == 0 and bool(enabled.value)
            except (AttributeError, OSError):
                return False

        def _restore_position(self) -> None:
            saved_x = self._settings.value("ui/overlayX", None)
            saved_y = self._settings.value("ui/overlayY", None)
            if saved_x is None or saved_y is None:
                screen = QApplication.primaryScreen()
                if screen is not None:
                    area = screen.availableGeometry()
                    self.move(
                        area.center().x() - self.width() // 2, area.bottom() - self.height() - 24
                    )
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
            x = max(area.left(), min(area.right() - self.width() + 1, point.x()))
            y = max(area.top(), min(area.bottom() - self.height() + 1, point.y()))
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
            handle = self.windowHandle()
            if handle is not None and getattr(self, "_screen_handle", None) is not handle:
                self._screen_handle = handle
                handle.screenChanged.connect(self._screen_changed)

        def _screen_changed(self, _screen: object) -> None:
            self._resize_to_screen()
            point = self._clamped_position(self.pos())
            if point != self.pos():
                self.move(point)

        def keyPressEvent(self, event: QKeyEvent) -> None:
            if event.key() == Qt.Key.Key_Escape and self._state.state is AppStatus.COMPLETED:
                self.close_result_requested.emit()
                event.accept()
                return
            super().keyPressEvent(event)

        def closeEvent(self, event: QCloseEvent) -> None:
            self._hiding = False
            AnimationCoordinator.instance().cancel(self.panel, "opacity")
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
            if getattr(self, "_styled_hwnd", None) == hwnd:
                return
            self._styled_hwnd = hwnd
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
