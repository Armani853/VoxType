"""Native Qt building blocks shared by VoxType's Obsidian interface."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from functools import lru_cache
from itertools import chain, repeat
from typing import ClassVar

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from .motion import AnimationCoordinator
from .theme import theme_manager, typography
from .widgets import Card, StatusDot


class IconProvider:
    _glyphs: ClassVar[dict[str, str]] = {
        "home": "\ue80f",
        "mic": "\ue720",
        "stop": "\ue71a",
        "history": "\ue81c",
        "files": "\ue8a5",
        "profiles": "\ue77b",
        "dictionary": "\ue82d",
        "snippets": "\ue8c8",
        "workflow": "\ue9ca",
        "settings": "\ue713",
        "about": "\ue946",
        "search": "\ue721",
        "menu": "\ue700",
        "close": "\ue711",
        "check": "\ue73e",
        "warning": "\ue7ba",
        "pause": "\ue769",
    }

    @classmethod
    def icon(
        cls, name: str, size: int = 18, dpr: float = 1, *, color_token: str = "text_secondary"
    ) -> QIcon:
        manager = theme_manager(QApplication.instance())
        return cls._pixmap(
            name, manager.current_theme.value, size, round(dpr, 2), manager.get_token(color_token)
        )

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
            paint.drawText(
                QRectF(0, 0, size, size),
                Qt.AlignmentFlag.AlignCenter,
                cls._glyphs.get(name, "\ue946"),
            )
        else:
            cls._draw_outline(paint, name, size, color)
        paint.end()
        return QIcon(image)

    @staticmethod
    @lru_cache(maxsize=1)
    def _icon_font() -> str:
        families = set(QFontDatabase.families())
        return next(
            (
                family
                for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                if family in families
            ),
            "",
        )

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
        elif name == "pause":
            paint.drawLine(7, 5, 7, 15)
            paint.drawLine(13, 5, 13, 15)
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


class VoxButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, variant: str = "") -> None:
        super().__init__(text, parent)
        self._fill = QColor()
        self._ripple = 1.0
        self._ripple_origin = QPointF()
        self._press_origin: QPointF | None = None
        self.setAccessibleName(text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(40)
        if variant:
            self.setProperty(variant, True)
        if variant == "primary":
            self.setMinimumHeight(44)
        self.pressed.connect(self._press)
        self.released.connect(self._release)
        self.toggled.connect(self._refresh_color)
        theme_manager(QApplication.instance()).theme_changed.connect(self._theme_changed)
        self._theme_changed()

    def _theme_changed(self, _theme: object = None) -> None:
        AnimationCoordinator.instance().cancel(self, "_fill")
        self._fill = self._target_color()
        self.update()

    def set_voice_mode(self, mode: str) -> None:
        if self.property("voiceMode") != mode:
            self.setProperty("voiceMode", mode)
            self._refresh_color()

    def _target_color(self) -> QColor:
        theme = theme_manager(QApplication.instance())
        mode = self.property("voiceMode")
        primary = bool(self.property("primary"))
        if not self.isEnabled() or mode == "processing":
            token = "surface_raised"
        elif mode == "error" or self.property("danger"):
            token = "danger" if primary else "surface_hover" if self.underMouse() else "surface"
        elif primary:
            token = (
                "accent_pressed"
                if self.isDown()
                else "accent_hover"
                if self.underMouse()
                else "accent_pressed"
                if mode == "recording"
                else "accent"
            )
        elif self.property("navRow") and self.isChecked():
            token = "accent_soft"
        elif self.isDown():
            token = "surface_raised"
        elif self.underMouse():
            token = "surface_hover"
        elif self.property("navRow") or self.property("flat"):
            return QColor(0, 0, 0, 0)
        else:
            token = "surface"
        return QColor(theme.get_token(token))

    def _refresh_color(self, *_args: object) -> None:
        coordinator = AnimationCoordinator.instance()
        coordinator.animate_color(
            self,
            "_fill",
            self._fill,
            self._target_color(),
            coordinator.PRESS if self.isDown() else coordinator.HOVER,
            easing=QEasingCurve.Type.OutQuad if self.isDown() else QEasingCurve.Type.OutCubic,
        )

    def _press(self) -> None:
        self._refresh_color()
        coordinator = AnimationCoordinator.instance()
        point = self._press_origin
        self._press_origin = None
        if (
            self.property("primary") or self.property("navRow")
        ) and not coordinator.reduced_motion_enabled():
            if point is None or not self.rect().contains(point.toPoint()):
                point = QPointF(self.rect().center())
            self._ripple_origin = point
            coordinator.animate_value(
                self,
                0.0,
                1.0,
                self._set_ripple,
                coordinator.RIPPLE,
                channel="ripple",
                retarget=False,
            )

    def _release(self) -> None:
        self._refresh_color()

    def _set_ripple(self, value: float) -> None:
        self._ripple = value
        self.update()

    def mousePressEvent(self, event: object) -> None:
        self._press_origin = event.position()
        super().mousePressEvent(event)
        self._press_origin = None

    def enterEvent(self, event: object) -> None:
        super().enterEvent(event)
        self._refresh_color()

    def leaveEvent(self, event: object) -> None:
        super().leaveEvent(event)
        self._refresh_color()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and hasattr(self, "_fill"):
            self._refresh_color()

    def event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.DynamicPropertyChange
            and hasattr(self, "_fill")
            and event.propertyName() in (b"primary", b"danger", b"flat", b"navRow")
        ):
            self._refresh_color()
        return super().event(event)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self._theme_changed()

    def paintEvent(self, _event: object) -> None:
        options = QStyleOptionButton()
        self.initStyleOption(options)
        theme = theme_manager(QApplication.instance())
        voice = bool(self.property("voiceControl"))
        nav = bool(self.property("navRow"))
        primary = bool(self.property("primary"))
        flat = bool(self.property("flat"))
        radius = 36 if voice else 8
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        outline = QPainterPath()
        outline.addRoundedRect(bounds, radius, radius)
        paint.fillPath(outline, self._fill)
        if self._ripple < 1:
            paint.save()
            paint.setClipPath(outline)
            tint = QColor(theme.get_token("text_primary" if nav else "canvas"))
            tint.setAlphaF(0.19 * (1 - self._ripple))
            paint.setPen(Qt.PenStyle.NoPen)
            paint.setBrush(tint)
            radius_now = max(self.width(), self.height()) * (0.18 + 0.94 * self._ripple)
            paint.drawEllipse(self._ripple_origin, radius_now, radius_now)
            paint.restore()
        if self.hasFocus() or not (nav or flat or primary):
            paint.setPen(
                QPen(
                    QColor(theme.get_token("accent" if self.hasFocus() else "border")),
                    2 if self.hasFocus() else 1,
                )
            )
            paint.setBrush(Qt.BrushStyle.NoBrush)
            paint.drawPath(outline)

        contrasting = primary and self.isEnabled() and self.property("voiceMode") != "processing"
        color = QColor(theme.get_token("text_primary" if self.isEnabled() else "text_muted"))
        if contrasting:
            color = QColor(
                theme.get_token("canvas") if theme.current_theme.value == "dark" else "#FFFFFF"
            )
        elif self.property("danger") and self.isEnabled():
            color = QColor(theme.get_token("danger"))
        options.palette.setColor(QPalette.ColorRole.ButtonText, color)
        if nav and self.text():
            inset = 11
            icon = self.icon()
            icon.paint(paint, inset, (self.height() - 18) // 2, 18, 18)
            paint.setPen(color)
            text_rect = self.rect().adjusted(inset + 27, 0, -6, 0)
            paint.drawText(
                text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text()
            )
        else:
            self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, options, paint, self)


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

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "icon_name"):
            self.refresh_icon()
        return handled


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


class AnimatedStatusDot(StatusDot):
    def set_color(self, color: str) -> None:
        target = QColor(color)
        if target == getattr(self, "_target", None):
            return
        self._target = target
        coordinator = AnimationCoordinator.instance()
        coordinator.animate_color(self, "_color", self._color, target, coordinator.HOVER)


class VoxToggle(QCheckBox):
    """A light track over native QCheckBox input and accessibility behavior."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAccessibleName(text)
        self.setMinimumHeight(38)
        self._position = 0.0
        self.toggled.connect(self._transition)

    def _transition(self, checked: bool) -> None:
        motion = AnimationCoordinator.instance()
        motion.animate_value(
            self, self._position, float(checked), self._move_thumb, motion.SWITCH, channel="thumb"
        )

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(hint.width() + 25)
        return hint

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
        paint.setBrush(
            QColor.fromRgbF(
                track.redF() * (1 - mix) + accent.redF() * mix,
                track.greenF() * (1 - mix) + accent.greenF() * mix,
                track.blueF() * (1 - mix) + accent.blueF() * mix,
            )
        )
        paint.setPen(
            QPen(
                QColor(manager.get_token("accent" if self.hasFocus() else "border")),
                2 if self.hasFocus() else 1,
            )
        )
        paint.drawRoundedRect(QRectF(1, y, 32, 18), 9, 9)
        paint.setPen(Qt.PenStyle.NoPen)
        paint.setBrush(
            QColor(manager.get_token("text_muted" if not self.isEnabled() else "text_primary"))
        )
        paint.drawEllipse(QRectF(4 + 14 * self._position, y + 3, 12, 12))
        option.rect = option.rect.adjusted(43, 0, 0, 0)
        self.style().drawControl(QStyle.ControlElement.CE_CheckBoxLabel, option, paint, self)

    def hitButton(self, position: object) -> bool:
        return self.rect().contains(position)


class VoxComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(38)
        self.setMaxVisibleItems(9)
        self.view().setMaximumHeight(360)


class VoxTextField(QLineEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(38)
        self.setProperty("animatedFocus", True)
        self._focus_border = QColor()
        theme_manager(QApplication.instance()).theme_changed.connect(self._update_border)
        self._update_border()

    def set_invalid(self, message: str = "") -> None:
        desired = bool(message)
        self.setAccessibleDescription(message)
        if bool(self.property("invalid")) != desired:
            self.setProperty("invalid", desired)
            self._update_border()

    def _update_border(self, *_args: object) -> None:
        theme = theme_manager(QApplication.instance())
        target = QColor(
            theme.get_token(
                "danger" if self.property("invalid") else "accent" if self.hasFocus() else "border"
            )
        )
        if not self._focus_border.isValid():
            self._focus_border = target
        coordinator = AnimationCoordinator.instance()
        coordinator.animate_color(
            self, "_focus_border", self._focus_border, target, coordinator.HOVER
        )

    def focusInEvent(self, event: object) -> None:
        super().focusInEvent(event)
        self._update_border()

    def focusOutEvent(self, event: object) -> None:
        super().focusOutEvent(event)
        self._update_border()

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)
        if not self.isEnabled():
            return
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setPen(QPen(self._focus_border, 1))
        paint.setBrush(Qt.BrushStyle.NoBrush)
        paint.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


class VoxSearchField(VoxTextField):
    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)
        self.setClearButtonEnabled(True)


class VoxBusyIndicator(QProgressBar):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(label)
        self.setTextVisible(False)
        self.setRange(0, 4)
        self.setValue(1)
        AnimationCoordinator.instance().preference_updated.connect(self._sync_animation)

    def _sync_animation(self) -> None:
        if self.isVisible() and not AnimationCoordinator.instance().reduced_motion_enabled():
            self.setRange(0, 0)
        else:
            self.setRange(0, 4)
            self.setValue(1)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event: object) -> None:
        self.setRange(0, 4)
        self.setValue(1)
        super().hideEvent(event)


class VoxSettingRow(QWidget):
    def __init__(self, title: str, control: QWidget, *, detail: str = "") -> None:
        super().__init__()
        self.setMinimumHeight(56)
        self._highlight = QColor(0, 0, 0, 0)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(16)
        captions = QVBoxLayout()
        captions.setSpacing(2)
        label = VoxSectionHeader(title, self, role="emphasis")
        label.setBuddy(control)
        captions.addWidget(label)
        if detail:
            description = QLabel(detail, self)
            description.setTextFormat(Qt.TextFormat.PlainText)
            description.setProperty("muted", True)
            description.setWordWrap(True)
            captions.addWidget(description)
            if not control.accessibleDescription():
                control.setAccessibleDescription(detail)
        if not control.accessibleName():
            control.setAccessibleName(title)
        row.addLayout(captions, 1)
        row.addWidget(control, 1)

    def enterEvent(self, event: object) -> None:
        super().enterEvent(event)
        shade = QColor(theme_manager(QApplication.instance()).get_token("surface_hover"))
        shade.setAlpha(150)
        self._fade(shade)

    def leaveEvent(self, event: object) -> None:
        super().leaveEvent(event)
        self._fade(QColor(0, 0, 0, 0))

    def _fade(self, color: QColor) -> None:
        motion = AnimationCoordinator.instance()
        motion.animate_color(self, "_highlight", self._highlight, color, motion.HOVER)

    def paintEvent(self, _event: object) -> None:
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setBrush(self._highlight)
        paint.setPen(Qt.PenStyle.NoPen)
        paint.drawRoundedRect(QRectF(self.rect()), 8, 8)


class VoxDisclosure(QWidget):
    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = content
        self._button = VoxButton(title, self, variant="flat")
        self._button.setCheckable(True)
        self._button.toggled.connect(self._show_content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(content)
        self._button.setChecked(True)

    def _show_content(self, expanded: bool) -> None:
        content = self._content
        self._button.setAccessibleDescription("Раздел раскрыт" if expanded else "Раздел свёрнут")
        old_height = content.height() if not content.isHidden() else 0
        content.setEnabled(expanded)
        if expanded:
            content.setMaximumHeight(old_height)
            content.show()
        target = content.sizeHint().height() if expanded else 0
        motion = AnimationCoordinator.instance()

        def settle() -> None:
            if expanded:
                content.setMaximumHeight(16_777_215)
            else:
                content.hide()

        motion.animate_value(
            content,
            old_height,
            target,
            content.setMaximumHeight,
            motion.PAGE,
            channel="disclosure",
            finished=settle,
        )


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
        self._idle_levels = tuple(
            (0.14 + 0.15 * (1 - abs(index * 2 / max(1, bars - 1) - 1))) * (0.65 if index % 2 else 1)
            for index in range(bars)
        )
        manager = theme_manager(QApplication.instance())
        self._active_brush = QBrush(QColor(manager.get_token("accent")))
        self._idle_brush = QBrush(QColor(manager.get_token("border")))
        manager.theme_changed.connect(self._theme_changed)
        motion = AnimationCoordinator.instance()
        self._static = motion.reduced_motion_enabled()
        motion.preference_updated.connect(self._motion_changed)

    def _motion_changed(self) -> None:
        static = AnimationCoordinator.instance().reduced_motion_enabled()
        if self._static != static:
            self._static = static
            self._values.clear()
            if self.isVisible():
                self.update()

    def _theme_changed(self, _theme: object) -> None:
        manager = theme_manager(QApplication.instance())
        self._active_brush.setColor(QColor(manager.get_token("accent")))
        self._idle_brush.setColor(QColor(manager.get_token("border")))
        self.update()

    def set_level(self, level: float, *_args: object) -> None:
        now = time.monotonic()
        if not self._active or self._static or not self.isVisible() or now - self._last < 1 / 30:
            return
        level = max(0.0, min(float(level), 1.0))
        coefficient = 0.35 if level >= self._level else 0.15
        self._level += coefficient * (level - self._level)
        self._values.append(self._level)
        self._last = now
        width = self._bars * 6 - 3
        self.update((self.width() - width) // 2, 0, width, self.height())

    @property
    def level(self) -> float:
        return self._level

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._values.clear()
            self._level = 0.0
            self._last = 0.0
            self.update()

    def paintEvent(self, _event: object) -> None:
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setPen(Qt.PenStyle.NoPen)
        paint.setBrush(self._active_brush if self._active else self._idle_brush)
        values = (
            chain(repeat(0.0, self._bars - len(self._values)), self._values)
            if self._active and not self._static
            else self._idle_levels
        )
        x = (self.width() - (self._bars * 6 - 3)) / 2
        for index, sample in enumerate(values):
            height = max(3.0, sample * (self.height() - 6))
            paint.drawRoundedRect(
                QRectF(x + index * 6, (self.height() - height) / 2, 3, height), 1.5, 1.5
            )


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
        accent.setAlpha(135)
        paint.setBrush(Qt.BrushStyle.NoBrush)
        paint.setPen(QPen(accent, 1.5))
        paint.drawEllipse(QPointF(48, 48), 41, 41)


class _ToastSlot(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.card = VoxCard(self)
        self.card.content_layout.setContentsMargins(14, 10, 12, 10)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._offset = 0

    def sizeHint(self) -> QSize:
        return self.card.sizeHint()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(
            self.card.minimumSizeHint().height(), self.card.content_layout.heightForWidth(width)
        )

    def set_offset(self, value: int) -> None:
        self._offset = value
        self.card.move(self._offset, 0)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self.card.resize(self.size())
        self.card.move(self._offset, 0)


class NotificationCenter(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedWidth(360)
        self.items: dict[str, _ToastSlot] = {}
        self.messages: dict[str, tuple[str, str, str]] = {}
        self.timers: dict[str, QTimer] = {}
        self._exiting: set[str] = set()
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(8, 8, 8, 8)
        self.hide()

    def notify(self, key: str, text: str, kind: str = "info", *, details: str = "") -> None:
        heading = {
            "success": "Готово",
            "info": "Уведомление",
            "warning": "Внимание",
            "error": "Ошибка",
        }[kind]
        previous = self.messages.get(key)
        if previous is not None and previous[0] != heading:
            self.dismiss(key, immediate=True)
        self.messages[key] = (heading, text, details)
        slot = self.items.get(key)
        motion = AnimationCoordinator.instance()
        if slot is not None:
            slot.card.findChild(QLabel, "toastText").setText(f"{heading} · {text}")
            more = slot.card.findChild(QPushButton, "toastDetails")
            more.setVisible(bool(details))
            if key in self.timers:
                self.timers[key].start()
            if key in self._exiting:
                self._exiting.remove(key)
                motion.animate_opacity(slot.card, 0.0, 1.0, motion.TOAST_ENTER)
                motion.animate_value(
                    slot, 10, 0, slot.set_offset, motion.TOAST_ENTER, channel="slide"
                )
        else:
            if len(self.items) >= 3:
                oldest = next(
                    (
                        entry
                        for entry in self.items
                        if entry in self.timers or entry in self._exiting
                    ),
                    None,
                )
                if oldest is None:
                    self.messages.pop(key, None)
                    return
                self.dismiss(oldest, immediate=True)
            slot = _ToastSlot(self)
            card = slot.card
            line = QHBoxLayout()
            if kind in {"warning", "error", "success"}:
                icon = QLabel(card)
                icon.setPixmap(
                    IconProvider.icon(
                        "check" if kind == "success" else "warning",
                        16,
                        icon.devicePixelRatioF(),
                        color_token="success"
                        if kind == "success"
                        else "danger"
                        if kind == "error"
                        else "warning",
                    ).pixmap(16, 16)
                )
                line.addWidget(icon)
            label = QLabel(f"{heading} · {text}", card)
            label.setObjectName("toastText")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            line.addWidget(label, 1)
            close = VoxIconButton("close", "Закрыть уведомление", card)
            close.clicked.connect(lambda: self.dismiss(key))
            line.addWidget(close)
            card.content_layout.addLayout(line)
            more = VoxButton("Подробнее…", card, variant="flat")
            more.setObjectName("toastDetails")
            more.clicked.connect(lambda: self._show_details(*self.messages[key]))
            card.content_layout.addWidget(more, alignment=Qt.AlignmentFlag.AlignLeft)
            more.setVisible(bool(details))
            self.column.addWidget(slot)
            self.items[key] = slot
            if kind in {"success", "info"}:
                timer = QTimer(slot, interval=4000, singleShot=True)
                self.timers[key] = timer
                timer.timeout.connect(lambda: self.dismiss(key))
                timer.start()
            self.show()
            slot.show()
            motion.animate_value(slot, 10, 0, slot.set_offset, motion.TOAST_ENTER, channel="slide")
            motion.animate_opacity(card, 0.0, 1.0, motion.TOAST_ENTER)
        self.reposition()

    def dismiss(self, key: str, *, immediate: bool = False) -> None:
        slot = self.items.get(key)
        if slot is None:
            return
        if key in self.timers:
            self.timers[key].stop()
        motion = AnimationCoordinator.instance()
        if immediate:
            motion.cancel_all(slot)
            motion.cancel_all(slot.card)
            self._remove(key)
        elif key not in self._exiting:
            self._exiting.add(key)
            motion.animate_value(slot, 0, 10, slot.set_offset, motion.TOAST_EXIT, channel="slide")
            motion.animate_opacity(
                slot.card, 1.0, 0.0, motion.TOAST_EXIT, finished=lambda: self._remove(key)
            )

    def _remove(self, key: str) -> None:
        self._exiting.discard(key)
        self.messages.pop(key, None)
        self.timers.pop(key, None)
        slot = self.items.pop(key, None)
        if slot is not None:
            self.column.removeWidget(slot)
            slot.hide()
            slot.deleteLater()
        self.setVisible(bool(self.items))
        self.reposition()

    def reposition(self) -> None:
        self.adjustSize()
        self.move(max(0, self.parentWidget().width() - self.width() - 16), 72)
        self.raise_()

    def _show_details(self, title: str, text: str, details: str) -> None:
        dialog = VoxMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDetailedText(details)
        dialog.exec()


class VoxDialog(QDialog):
    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        motion = AnimationCoordinator.instance()
        motion.animate_opacity(self, 0.70, 1.0, motion.MODAL)


class VoxMessageBox(QMessageBox):
    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        motion = AnimationCoordinator.instance()
        motion.animate_opacity(self, 0.70, 1.0, motion.MODAL)


def confirm_delete(parent: QWidget, title: str, detail: str) -> bool:
    dialog = VoxMessageBox(QMessageBox.Icon.Warning, title, detail, parent=parent)
    action = dialog.addButton("Удалить", QMessageBox.ButtonRole.DestructiveRole)
    action.setProperty("danger", True)
    cancel = dialog.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel)
    dialog.setEscapeButton(cancel)
    dialog.exec()
    return dialog.clickedButton() is action


class ModelDownloadDialog(VoxDialog):
    def __init__(
        self, model_name: str, parent: QWidget, cancel: Callable[[], None] | None = None
    ) -> None:
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
        if cancel is not None:
            stop = VoxButton("Отменить загрузку", self)
            stop.clicked.connect(cancel)
            layout.addWidget(stop, alignment=Qt.AlignmentFlag.AlignRight)
        close = VoxButton("Скрыть", self)
        close.clicked.connect(self.hide)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def update_progress(self, percent: int, message: str) -> None:
        if percent > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        if message:
            self.detail.setText(message)
