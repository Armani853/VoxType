"""Native Qt building blocks shared by VoxType's Obsidian interface."""

from __future__ import annotations

import ctypes
import sys
import time
from collections import deque
from functools import lru_cache

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QStyle,
    QStyleOptionButton, QVBoxLayout, QWidget,
)

from .theme import theme_manager, typography
from .widgets import Card


class IconProvider:
    _glyphs = {
        "home": "\ue80f", "mic": "\ue720", "stop": "\ue71a", "history": "\ue81c",
        "files": "\ue8a5", "profiles": "\ue77b", "dictionary": "\ue82d",
        "snippets": "\ue8c8", "workflow": "\ue9ca", "settings": "\ue713",
        "about": "\ue946", "search": "\ue721", "menu": "\ue700", "close": "\ue711",
        "check": "\ue73e", "warning": "\ue7ba",
    }

    @classmethod
    def icon(cls, name: str, size: int = 18, dpr: float = 1, *, color_token: str = "text_secondary") -> QIcon:
        manager = theme_manager(QApplication.instance())
        return cls._pixmap(name, manager.current_theme.value, size, round(dpr, 2), manager.get_token(color_token))

    @classmethod
    @lru_cache(maxsize=256)
    def _pixmap(cls, name: str, theme: str, size: int, dpr: float, color: str) -> QIcon:
        image = QPixmap(round(size * dpr), round(size * dpr))
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        paint = QPainter(image)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setPen(QColor(color))
        family = cls._icon_font()
        if family:
            font = QFont(family)
            font.setPixelSize(size - 1)
            paint.setFont(font)
            paint.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, cls._glyphs.get(name, "\ue946"))
        else:
            cls._draw_outline(paint, name, size, color)
        paint.end()
        return QIcon(image)

    @staticmethod
    @lru_cache(maxsize=1)
    def _icon_font() -> str:
        families = set(QFontDatabase.families())
        return next((family for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if family in families), "")

    @staticmethod
    def _draw_outline(paint: QPainter, name: str, size: int, color: str) -> None:
        paint.scale(size / 20, size / 20)
        pen = QPen(QColor(color), 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        paint.setPen(pen)
        if name == "mic":
            paint.drawRoundedRect(QRectF(7, 2, 6, 10), 3, 3)
            paint.drawArc(QRectF(4, 5, 12, 10), 180 * 16, 180 * 16)
            paint.drawLine(10, 15, 10, 18)
        elif name == "search":
            paint.drawEllipse(QRectF(3, 3, 9, 9))
            paint.drawLine(11, 11, 16, 16)
        elif name == "close":
            paint.drawLine(5, 5, 15, 15)
            paint.drawLine(5, 15, 15, 5)
        elif name == "menu":
            for y in (5, 10, 15):
                paint.drawLine(3, y, 17, y)
        elif name == "stop":
            paint.drawRoundedRect(QRectF(5, 5, 10, 10), 2, 2)
        elif name == "home":
            paint.drawLine(3, 9, 10, 3)
            paint.drawLine(10, 3, 17, 9)
            paint.drawRect(QRectF(5, 9, 10, 8))
        elif name == "history":
            paint.drawEllipse(QRectF(3, 3, 14, 14))
            paint.drawLine(10, 6, 10, 10)
            paint.drawLine(10, 10, 13, 12)
        else:
            paint.drawRoundedRect(QRectF(4, 3, 12, 14), 2, 2)
            paint.drawLine(7, 8, 13, 8)
            paint.drawLine(7, 12, 13, 12)


class MotionController:
    @staticmethod
    def allowed() -> bool:
        app = QApplication.instance()
        if app is None or app.property("voxtypeReduceMotion"):
            return False
        if sys.platform == "win32":
            enabled = ctypes.c_int()
            try:
                if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                    return bool(enabled.value)
            except (AttributeError, OSError):
                pass
        return True

    @classmethod
    def fade(cls, widget: QWidget, duration: int, start: float = 0.55) -> None:
        if not cls.allowed():
            return
        old = widget.graphicsEffect()
        if old is not None:
            widget.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(start)
        animation = QVariantAnimation(effect)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(effect.setOpacity)
        animation.finished.connect(lambda: QTimer.singleShot(0, widget, lambda: cls._clear(widget, effect)))
        animation.start()

    @staticmethod
    def _clear(widget: QWidget, effect: QGraphicsOpacityEffect) -> None:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)


class VoxButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, variant: str = "") -> None:
        super().__init__(text, parent)
        self.setAccessibleName(text)
        if variant:
            self.setProperty(variant, True)
        if variant == "primary":
            self.setMinimumHeight(44)


class VoxIconButton(VoxButton):
    def __init__(self, icon: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__("", parent, variant="flat")
        self.icon_name = icon
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setFixedSize(38, 38)
        theme_manager(QApplication.instance()).theme_changed.connect(self.refresh_icon)
        self.refresh_icon()

    def refresh_icon(self, _theme: object = None) -> None:
        self.setIcon(IconProvider.icon(self.icon_name, 18, self.devicePixelRatioF()))


VoxCard = Card


class VoxSectionHeader(QLabel):
    def __init__(self, title: str, parent: QWidget | None = None, *, role: str = "section") -> None:
        super().__init__(title, parent)
        self.setFont(typography(role))
        self.setWordWrap(True)


class VoxStatusBadge(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(typography("caption"))
        self.setProperty("statusBadge", "success")


class VoxToggle(QCheckBox):
    """A light track over native QCheckBox input and accessibility behavior."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAccessibleName(text)
        self.setMinimumHeight(38)
        self._position = 0.0
        self._slide = QVariantAnimation(self)
        self._slide.valueChanged.connect(self._move_thumb)
        self.toggled.connect(self._transition)

    def _transition(self, checked: bool) -> None:
        self._slide.stop()
        destination = float(checked)
        if MotionController.allowed() and self.isVisible():
            self._slide.setDuration(120)
            self._slide.setStartValue(self._position)
            self._slide.setEndValue(destination)
            self._slide.start()
        else:
            self._move_thumb(destination)

    def _move_thumb(self, value: object) -> None:
        self._position = float(value)
        self.update()

    def paintEvent(self, _event: object) -> None:
        manager = theme_manager(QApplication.instance())
        option = QStyleOptionButton()
        self.initStyleOption(option)
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - 18) / 2
        accent = QColor(manager.get_token("accent_hover" if self.underMouse() else "accent"))
        track = QColor(manager.get_token("surface_raised"))
        mix = self._position if self.isEnabled() else 0.0
        paint.setBrush(QColor.fromRgbF(
            track.redF() * (1 - mix) + accent.redF() * mix,
            track.greenF() * (1 - mix) + accent.greenF() * mix,
            track.blueF() * (1 - mix) + accent.blueF() * mix,
        ))
        paint.setPen(QPen(QColor(manager.get_token("accent" if self.hasFocus() else "border")),
                          2 if self.hasFocus() else 1))
        paint.drawRoundedRect(QRectF(1, y, 32, 18), 9, 9)
        paint.setPen(Qt.PenStyle.NoPen)
        paint.setBrush(QColor(manager.get_token("text_muted" if not self.isEnabled() else "text_primary")))
        paint.drawEllipse(QRectF(4 + 14 * self._position, y + 3, 12, 12))
        option.rect = option.rect.adjusted(43, 0, 0, 0)
        self.style().drawControl(QStyle.ControlElement.CE_CheckBoxLabel, option, paint, self)

    def hitButton(self, position: object) -> bool:
        return self.rect().contains(position)


class VoxComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(38)


class VoxTextField(QLineEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(38)

    def set_invalid(self, message: str = "") -> None:
        desired = bool(message)
        self.setAccessibleDescription(message)
        if bool(self.property("invalid")) != desired:
            self.setProperty("invalid", desired)
            self.style().unpolish(self)
            self.style().polish(self)


class VoxSearchField(VoxTextField):
    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)
        self.setClearButtonEnabled(True)


class VoxEmptyState(QWidget):
    def __init__(self, icon: str, title: str, detail: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 32, 24, 32)
        column.addStretch()
        self._symbol = icon
        self._mark = QLabel(self)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self._mark)
        theme_manager(QApplication.instance()).theme_changed.connect(self._update_icon)
        self._update_icon()
        for heading, text in ((True, title), (False, detail)):
            label = VoxSectionHeader(text, self) if heading else QLabel(text, self)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setProperty("muted", not heading)
            column.addWidget(label)
        self.action = VoxButton("", self)
        self.action.hide()
        column.addWidget(self.action, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addStretch()

    def _update_icon(self, _theme: object = None) -> None:
        dpr = self._mark.devicePixelRatioF()
        self._mark.setPixmap(IconProvider.icon(self._symbol, 24, dpr).pixmap(24, 24))


class VoxSeparator(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFixedHeight(1)


class VoxShortcutBadge(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(typography("caption"))
        self.setProperty("keyHint", True)


class WaveformWidget(QWidget):
    def __init__(self, parent: QWidget | None = None, *, bars: int = 31, height: int = 42) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMinimumWidth(bars * 6 - 3)
        self._bars = bars
        self._values: deque[float] = deque(maxlen=bars)
        self._active = False
        self._last = 0.0
        self._level = 0.0
        theme_manager(QApplication.instance()).theme_changed.connect(self._theme_changed)

    def _theme_changed(self, _theme: object) -> None:
        self.update()

    def set_level(self, level: float, *_args: object) -> None:
        now = time.monotonic()
        if not self._active or now - self._last < 1 / 30:
            return
        level = max(0.0, min(float(level), 1.0))
        previous = self._values[-1] if self._values else 0.0
        self._values.append(max(level, previous * 0.80))
        self._level = level
        self._last = now
        self.update()

    @property
    def level(self) -> float:
        return self._level

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._values.clear()
            self._level = 0.0
            self.update()

    def paintEvent(self, _event: object) -> None:
        manager = theme_manager(QApplication.instance())
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setPen(Qt.PenStyle.NoPen)
        paint.setBrush(QColor(manager.get_token("accent" if self._active else "border")))
        values = [0.0] * (self._bars - len(self._values)) + list(self._values)
        x = (self.width() - (self._bars * 6 - 3)) / 2
        for index, sample in enumerate(values):
            if not self._active:
                sample = (0.14 + 0.15 * (1 - abs(index * 2 / (self._bars - 1) - 1))) * (0.65 if index % 2 else 1)
            height = max(3.0, sample * (self.height() - 6))
            paint.drawRoundedRect(QRectF(x + index * 6, (self.height() - height) / 2, 3, height), 1.5, 1.5)


class VoiceHalo(QWidget):
    def __init__(self, button: QPushButton, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(96, 96)
        self._recording = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_recording(self, recording: bool) -> None:
        if self._recording != recording:
            self._recording = recording
            self.update()

    def paintEvent(self, _event: object) -> None:
        if not self._recording:
            return
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setPen(Qt.PenStyle.NoPen)
        accent = QColor(theme_manager(QApplication.instance()).get_token("accent"))
        for radius, alpha in ((47, 20), (43, 30), (39, 44)):
            accent.setAlpha(alpha)
            paint.setBrush(accent)
            paint.drawEllipse(QPointF(48, 48), radius, radius)


class NotificationCenter(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedWidth(360)
        self.items: dict[str, QFrame] = {}
        self.kinds: dict[str, str] = {}
        self.timers: dict[str, QTimer] = {}
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(8, 8, 8, 8)
        self.hide()

    def notify(self, key: str, text: str, kind: str = "info", *, details: str = "") -> None:
        if key in self.kinds and self.kinds[key] != kind:
            self.dismiss(key)
        if key in self.items:
            self.items[key].findChild(QLabel, "toastText").setText(text)
            if key in self.timers:
                self.timers[key].start()
        elif len(self.items) < 3 or any(key in self.timers for key in self.items):
            if len(self.items) >= 3:
                self.dismiss(next(key for key in self.items if key in self.timers))
            card = VoxCard(self)
            line = QHBoxLayout()
            heading = {"success": "Готово", "info": "Уведомление", "warning": "Внимание", "error": "Ошибка"}[kind]
            label = QLabel(f"{heading} · {text}", card)
            label.setObjectName("toastText")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            line.addWidget(label, 1)
            close = VoxIconButton("close", "Закрыть уведомление", card)
            close.clicked.connect(lambda: self.dismiss(key))
            line.addWidget(close)
            card.content_layout.addLayout(line)
            if details:
                more = VoxButton("Подробнее…", card, variant="flat")
                more.clicked.connect(lambda: self._show_details(heading, text, details))
                card.content_layout.addWidget(more, alignment=Qt.AlignmentFlag.AlignLeft)
            self.column.addWidget(card)
            self.items[key] = card
            self.kinds[key] = kind
            if kind in {"success", "info"}:
                timer = QTimer(card, interval=4000, singleShot=True)
                self.timers[key] = timer
                timer.timeout.connect(lambda: self.dismiss(key))
                timer.start()
            self.show()
            MotionController.fade(card, 180)
        self.reposition()

    def dismiss(self, key: str) -> None:
        self.kinds.pop(key, None)
        timer = self.timers.pop(key, None)
        if timer is not None:
            timer.stop()
        card = self.items.pop(key, None)
        if card is not None:
            self.column.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.setVisible(bool(self.items))
        self.reposition()

    def reposition(self) -> None:
        self.adjustSize()
        self.move(max(0, self.parentWidget().width() - self.width() - 16), 72)
        self.raise_()

    def _show_details(self, title: str, text: str, details: str) -> None:
        dialog = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDetailedText(details)
        dialog.exec()


def confirm_delete(parent: QWidget, title: str, detail: str) -> bool:
    dialog = QMessageBox(QMessageBox.Icon.Warning, title, detail, parent=parent)
    action = dialog.addButton("Удалить", QMessageBox.ButtonRole.DestructiveRole)
    action.setProperty("danger", True)
    cancel = dialog.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel)
    dialog.setEscapeButton(cancel)
    dialog.exec()
    return dialog.clickedButton() is action


class ModelDownloadDialog(QDialog):
    def __init__(self, model_name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Загрузка модели")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(VoxSectionHeader(model_name, self, role="page"))
        self.detail = QLabel("Подготовка загрузки…", self)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Прогресс загрузки модели")
        layout.addWidget(self.progress)
        close = VoxButton("Скрыть", self)
        close.clicked.connect(self.hide)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def update_progress(self, percent: int, message: str) -> None:
        if percent > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        if message:
            self.detail.setText(message)
