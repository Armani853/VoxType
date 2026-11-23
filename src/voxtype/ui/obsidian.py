"""Native Qt building blocks shared by VoxType's Obsidian interface."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from functools import lru_cache
from itertools import chain, repeat
from typing import ClassVar

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QEnterEvent,
    QFont,
    QFontDatabase,
    QHideEvent,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
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
    QStyleOptionComboBox,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .motion import DURATIONS, TOGGLE, AnimationCoordinator, InteractionVisualState, blend
from .theme import DARK, LIGHT, ThemeMode, theme_manager, typography
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
        "minimize": "\ue921",
        "expand": "\ue922",
        "restore": "\ue923",
        "copy": "\ue8c8",
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
        elif name == "minimize":
            paint.drawLine(4, 10, 16, 10)
        elif name == "expand":
            paint.drawRect(QRectF(4, 4, 12, 12))
        elif name == "restore":
            paint.drawRect(QRectF(4, 7, 9, 9))
            paint.drawLine(7, 4, 16, 4)
            paint.drawLine(16, 4, 16, 13)
        elif name == "copy":
            paint.drawRoundedRect(QRectF(7, 6, 9, 11), 1.5, 1.5)
            paint.drawLine(4, 13, 4, 3)
            paint.drawLine(4, 3, 12, 3)
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


@lru_cache(maxsize=48)
def _motion_color(theme: ThemeMode, token: str) -> QColor:
    return QColor((DARK if theme is ThemeMode.DARK else LIGHT)[token])


class VoxButton(QPushButton):
    def __init__(
        self,
        text: str,
        parent: QWidget | None = None,
        *,
        variant: str = "",
        overlay: bool = False,
    ) -> None:
        super().__init__(text, parent)
        self._ripple = 1.0
        self._ripple_origin = QPointF()
        self._press_origin: QPointF | None = None
        self._motion_icon: tuple[str, int] | None = None
        self._bright_icon = QIcon()
        self._overlay_button = overlay
        self._visual = InteractionVisualState(
            self,
            pointer=True,
            hover_duration=DURATIONS.border if variant == "primary" else DURATIONS.hover_enter,
        )
        self.setAccessibleName(text)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus if overlay else Qt.FocusPolicy.StrongFocus)
        if not overlay:
            self.setMinimumHeight(44 if variant == "primary" else 40)
        if variant:
            self.setProperty(variant, True)
        self.pressed.connect(self._press)
        self.released.connect(self._release)
        self.toggled.connect(self._visual.set_selected)
        theme_manager(QApplication.instance()).theme_changed.connect(self._theme_changed)
        self._theme_changed()

    def _theme_changed(self, _theme: object = None) -> None:
        self._theme = theme_manager(QApplication.instance()).current_theme
        self._refresh_motion_icon()
        if self.isVisible():
            self.update()

    def set_voice_mode(self, mode: str) -> None:
        if self.property("voiceMode") != mode:
            self.setProperty("voiceMode", mode)
            if mode in {"starting", "processing"}:
                AnimationCoordinator.instance().cancel(self, "ripple", settle=True)
            self.update()

    def set_motion_icon(self, name: str, size: int) -> None:
        self._motion_icon = (name, size)
        self._refresh_motion_icon()

    def _refresh_motion_icon(self) -> None:
        if self._motion_icon is None:
            return
        name, size = self._motion_icon
        dpr = self.devicePixelRatioF()
        self.setIconSize(QSize(size, size))
        tokens = DARK if self._theme is ThemeMode.DARK else LIGHT
        self.setIcon(
            IconProvider._pixmap(name, self._theme.value, size, dpr, tokens["text_secondary"])
        )
        self._bright_icon = IconProvider._pixmap(
            name, self._theme.value, size, dpr, tokens["text_primary"]
        )

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "_motion_icon"):
            self._refresh_motion_icon()
        return handled

    def _paint_icon(self, painter: QPainter, rect: QRect, emphasis: float) -> None:
        icon = self.icon()
        if not self.isEnabled():
            icon.paint(painter, rect, Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Disabled)
            return
        painter.save()
        icon.paint(painter, rect)
        if emphasis and not self._bright_icon.isNull():
            painter.setOpacity(painter.opacity() * emphasis)
            self._bright_icon.paint(painter, rect)
        painter.restore()

    def _press(self) -> None:
        self._visual.set_pressed(True)
        coordinator = AnimationCoordinator.instance()
        point = self._press_origin
        self._press_origin = None
        if (
            self.isEnabled()
            and (self.property("primary") or self.property("navRow"))
            and self.property("voiceMode") not in {"starting", "processing"}
            and not coordinator.reduced_motion_enabled()
        ):
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
        self._visual.set_pressed(False)

    def _set_ripple(self, value: float) -> None:
        self._ripple = value
        if self.isVisible():
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._press_origin = event.position()
        super().mousePressEvent(event)
        self._press_origin = None

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.EnabledChange
            and hasattr(self, "_visual")
            and not self.isEnabled()
        ):
            AnimationCoordinator.instance().cancel(self, "ripple", settle=True)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._visual.set_selected(self.isChecked())

    def paintEvent(self, _event: QPaintEvent) -> None:
        options = QStyleOptionButton()
        self.initStyleOption(options)
        options.state &= ~(QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_On)
        voice = bool(self.property("voiceControl"))
        nav = bool(self.property("navRow"))
        primary = bool(self.property("primary") or self.property("compactPrimary"))
        danger = bool(self.property("danger"))
        flat = bool(self.property("flat"))
        segment = bool(self.property("segment"))
        mode = self.property("voiceMode")
        hover, press, selected, focus = self._visual.values
        busy = mode in {"starting", "processing"}
        if busy:
            hover = press = 0.0

        def color(token: str) -> QColor:
            return _motion_color(self._theme, token)

        clear = QColor(0, 0, 0, 0)
        fill = clear if nav or flat or segment else color("surface")
        edge = clear if nav or flat or segment else color("motion_border")
        ink = color("text_secondary" if nav or segment else "text_primary")
        enabled = self.isEnabled()
        if not enabled:
            ink = color("text_muted")
            if not (nav or flat or segment):
                fill = color("surface_raised")
        elif primary and not busy:
            ink = color("canvas") if self._theme is ThemeMode.DARK else QColor("white")
            if mode == "error":
                fill = blend(color("danger"), color("motion_danger_hover"), hover)
                fill = blend(fill, color("danger"), press)
            else:
                accent = "motion_accent_pressed" if mode == "recording" else "accent"
                fill = blend(color(accent), color("motion_accent_hover"), hover)
                fill = blend(fill, color("motion_accent_pressed"), press)
            edge = blend(clear, color("motion_focus"), hover * 0.75)
        else:
            if busy:
                fill = color("surface_raised")
            elif segment and self.property("groupIndicator"):
                fill = blend(fill, color("motion_hover"), hover * (1.0 - selected))
            elif self.property("overlayExpand"):
                fill = blend(fill, color("motion_selected"), hover)
            else:
                fill = blend(fill, color("motion_selected"), selected)
                fill = blend(fill, color("motion_hover"), hover * (1.0 - 0.7 * selected))
            fill = blend(fill, color("motion_danger_fill" if danger else "motion_pressed"), press)
            if danger:
                fill = blend(fill, color("motion_danger_fill"), hover * 0.85)
                ink = blend(ink, color("motion_danger_hover"), max(hover, press))
            else:
                ink = blend(ink, color("text_primary"), max(hover, selected))
                if segment:
                    ink = blend(ink, color("accent"), selected)
            edge = blend(
                edge,
                color("motion_border_hover"),
                max(press, hover) * (0.5 if nav or flat else 1.0),
            )

        radius = 36 if voice else 8
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1.25, 1.25, -1.25, -1.25)
        outline = QPainterPath()
        outline.addRoundedRect(bounds, radius, radius)
        paint.fillPath(outline, fill)
        if enabled and self._ripple < 1:
            paint.save()
            paint.setClipPath(outline)
            tint = QColor(color("text_primary" if nav else "canvas"))
            tint.setAlphaF(0.14 * (1 - self._ripple))
            paint.setPen(Qt.PenStyle.NoPen)
            paint.setBrush(tint)
            radius_now = max(self.width(), self.height()) * (0.18 + 0.94 * self._ripple)
            paint.drawEllipse(self._ripple_origin, radius_now, radius_now)
            paint.restore()
        paint.setBrush(Qt.BrushStyle.NoBrush)
        paint.setPen(QPen(edge, 1))
        paint.drawPath(outline)
        if focus:
            ring = QColor(color("motion_focus"))
            ring.setAlphaF(focus)
            paint.setPen(QPen(ring, 2))
            paint.drawPath(outline)

        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            options.palette.setColor(group, QPalette.ColorRole.ButtonText, ink)
        if nav and self.text():
            inset = 11
            emphasis = max(hover, selected) if self._motion_icon is not None else 0.0
            self._paint_icon(paint, QRect(inset, (self.height() - 18) // 2, 18, 18), emphasis)
            paint.setPen(ink)
            text_rect = self.rect().adjusted(inset + 27, 0, -6, 0)
            paint.drawText(
                text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text()
            )
        elif self._motion_icon is not None and not self.text():
            icon_size = self.iconSize()
            icon_rect = QRect(QPoint(), icon_size)
            icon_rect.moveCenter(self.rect().center())
            self._paint_icon(paint, icon_rect, max(hover, selected, focus * 0.6))
        else:
            self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, options, paint, self)


class VoxIconButton(VoxButton):
    def __init__(
        self, icon: str, label: str, parent: QWidget | None = None, *, overlay: bool = False
    ) -> None:
        super().__init__("", parent, variant="flat", overlay=overlay)
        self.icon_name = icon
        self.setToolTip(label)
        self.setAccessibleName(label)
        side = 32 if overlay else 38
        self.setFixedSize(side, side)
        self.refresh_icon()

    def refresh_icon(self, _theme: object = None) -> None:
        size = 16 if self._overlay_button else 18
        self.set_motion_icon(self.icon_name, size)

    def set_icon_name(self, icon: str) -> None:
        if self.icon_name != icon:
            self.icon_name = icon
            self.refresh_icon()


class VoxCard(Card):
    def __init__(
        self, parent: QWidget | None = None, *, spacing: int = 10, emphasis: bool = False
    ) -> None:
        super().__init__(parent, spacing=spacing)
        self._emphasis = (
            InteractionVisualState(self, hover_duration=DURATIONS.card_emphasis)
            if emphasis
            else None
        )

    def set_selected(self, selected: bool) -> None:
        if self._emphasis is not None:
            self._emphasis.set_selected(selected)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._emphasis is None:
            super().paintEvent(event)
            return
        hover, _press, selected, _focus = self._emphasis.values
        mode = theme_manager(QApplication.instance()).current_theme
        base = _motion_color(mode, "surface")
        fill = blend(base, _motion_color(mode, "motion_selected"), selected * 0.65)
        fill = blend(fill, _motion_color(mode, "motion_hover"), hover * 0.45)
        edge = blend(
            _motion_color(mode, "motion_border"),
            _motion_color(mode, "motion_focus" if selected else "motion_border_hover"),
            max(selected * 0.75, hover * 0.5),
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


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
        self._visual = InteractionVisualState(self, pointer=True)
        self.pressed.connect(lambda: self._visual.set_pressed(True))
        self.released.connect(lambda: self._visual.set_pressed(False))
        self.toggled.connect(self._transition)

    def _transition(self, checked: bool) -> None:
        motion = AnimationCoordinator.instance()
        motion.animate_value(
            self,
            self._position,
            float(checked),
            self._move_thumb,
            DURATIONS.toggle,
            channel="thumb",
            easing=TOGGLE,
        )

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        hint.setWidth(hint.width() + 25)
        return hint

    def _move_thumb(self, value: float) -> None:
        self._position = float(value)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        manager = theme_manager(QApplication.instance())
        option = QStyleOptionButton()
        self.initStyleOption(option)
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - 18) / 2
        hover, press, _selected, focus = self._visual.values
        mode = manager.current_theme
        accent = blend(
            _motion_color(mode, "accent"), _motion_color(mode, "motion_accent_hover"), hover
        )
        track = blend(
            _motion_color(mode, "surface_raised"), _motion_color(mode, "motion_hover"), hover
        )
        mix = self._position if self.isEnabled() else 0.0
        fill = blend(track, accent, mix)
        paint.setBrush(blend(fill, _motion_color(mode, "motion_pressed"), press * 0.25))
        edge = blend(
            _motion_color(mode, "motion_border"), _motion_color(mode, "motion_border_hover"), hover
        )
        edge = blend(edge, _motion_color(mode, "motion_focus"), max(focus, mix * 0.35))
        paint.setPen(QPen(edge, 1 + focus))
        paint.drawRoundedRect(QRectF(1, y, 32, 18), 9, 9)
        paint.setPen(Qt.PenStyle.NoPen)
        paint.setBrush(
            QColor(manager.get_token("text_muted" if not self.isEnabled() else "text_primary"))
        )
        paint.drawEllipse(QRectF(4 + 14 * self._position, y + 3, 12, 12))
        option.rect = option.rect.adjusted(43, 0, 0, 0)
        self.style().drawControl(QStyle.ControlElement.CE_CheckBoxLabel, option, paint, self)

    def hitButton(self, position: QPoint) -> bool:
        return self.rect().contains(position)


def _paint_input_frame(widget: QWidget, visual: InteractionVisualState, paint: QPainter) -> None:
    hover, _press, invalid, focus = visual.values
    mode = theme_manager(QApplication.instance()).current_theme
    normal = _motion_color(mode, "motion_border")
    edge = blend(normal, _motion_color(mode, "motion_border_hover"), hover)
    edge = blend(edge, _motion_color(mode, "motion_focus"), focus)
    edge = blend(edge, _motion_color(mode, "danger"), invalid)
    paint.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    paint.setPen(QPen(edge, 1))
    paint.drawRoundedRect(rect, 8, 8)
    if focus:
        ring = QColor(_motion_color(mode, "motion_focus"))
        ring.setAlphaF(focus * 0.85)
        paint.setPen(QPen(ring, 1))
        paint.drawRoundedRect(rect.adjusted(1.4, 1.4, -1.4, -1.4), 7, 7)


class VoxComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(38)
        self.setMaxVisibleItems(9)
        self.view().setMaximumHeight(360)
        self.setProperty("animatedFocus", True)
        self._visual = InteractionVisualState(self, mouse_focus=True)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        paint = QPainter(self)
        if self.isEnabled():
            _paint_input_frame(self, self._visual, paint)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, self
        )
        if arrow.isValid():
            mode = theme_manager(QApplication.instance()).current_theme
            hover, _press, _invalid, focus = self._visual.values
            ink = (
                blend(
                    _motion_color(mode, "text_secondary"),
                    _motion_color(mode, "text_primary"),
                    max(hover, focus),
                )
                if self.isEnabled()
                else _motion_color(mode, "text_muted")
            )
            pen = QPen(ink, 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            paint.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint.setPen(pen)
            x, y = arrow.center().x(), arrow.center().y()
            paint.drawLine(QPointF(x - 3.5, y - 1.5), QPointF(x, y + 2))
            paint.drawLine(QPointF(x, y + 2), QPointF(x + 3.5, y - 1.5))


class VoxTextField(QLineEdit):
    def __init__(self, text: str | QWidget | None = None, parent: QWidget | None = None) -> None:
        super().__init__(
            text if isinstance(text, str) else "", text if isinstance(text, QWidget) else parent
        )
        self.setMinimumHeight(38)
        self.setProperty("animatedFocus", True)
        self._visual = InteractionVisualState(self, mouse_focus=True)

    def set_invalid(self, message: str = "") -> None:
        self.setAccessibleDescription(message)
        if bool(self.property("invalid")) != bool(message):
            self.setProperty("invalid", bool(message))
            self._visual.set_selected(bool(message))

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.isEnabled():
            paint = QPainter(self)
            _paint_input_frame(self, self._visual, paint)


class VoxCheckBox(QCheckBox):
    """Keep the native choice and label while painting the indicator in place."""

    def __init__(self, text: str | QWidget | None = None, parent: QWidget | None = None) -> None:
        super().__init__(
            text if isinstance(text, str) else "", text if isinstance(text, QWidget) else parent
        )
        self._visual = InteractionVisualState(self, pointer=True)
        self.pressed.connect(lambda: self._visual.set_pressed(True))
        self.released.connect(lambda: self._visual.set_pressed(False))
        self.toggled.connect(self._visual.set_selected)

    def paintEvent(self, _event: QPaintEvent) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        style = self.style()
        indicator = style.subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        contents = style.subElementRect(QStyle.SubElement.SE_CheckBoxContents, option, self)
        hover, press, marked, focus = self._visual.values
        partial = self.checkState() is Qt.CheckState.PartiallyChecked
        marked = max(marked, float(partial)) if self.isEnabled() else 0.0
        mode = theme_manager(QApplication.instance()).current_theme
        fill = blend(_motion_color(mode, "surface"), _motion_color(mode, "accent"), marked)
        fill = blend(fill, _motion_color(mode, "motion_pressed"), press * 0.45)
        edge = blend(
            _motion_color(mode, "motion_border"), _motion_color(mode, "motion_border_hover"), hover
        )
        edge = blend(edge, _motion_color(mode, "motion_focus"), max(focus, marked))
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setBrush(fill)
        paint.setPen(QPen(edge, 1 + focus))
        bounds = QRectF(indicator).adjusted(1, 1, -1, -1)
        paint.drawRoundedRect(bounds, 3, 3)
        strength = marked if self.isEnabled() else (0.6 if self.isChecked() else 0)
        if strength:
            ink = QColor("white" if mode is ThemeMode.LIGHT else "#101116")
            if not self.isEnabled():
                ink = QColor(_motion_color(mode, "text_secondary"))
            ink.setAlphaF(strength)
            pen = QPen(ink, 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            paint.setPen(pen)
            x, y = bounds.center().x(), bounds.center().y()
            paint.drawLine(
                QPointF(x - 3.5, y), QPointF(x + 3.5, y) if partial else QPointF(x - 1, y + 2.5)
            )
            if not partial:
                paint.drawLine(QPointF(x - 1, y + 2.5), QPointF(x + 3.5, y - 2.5))
        option.rect = contents
        style.drawControl(QStyle.ControlElement.CE_CheckBoxLabel, option, paint, self)


class VoxSearchField(VoxTextField):
    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)
        self.setClearButtonEnabled(True)


class _MotionTabBar(QTabBar):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("animatedIndicator", True)
        self._indicator = QRect()
        self._indicator_index = -1
        self._visual = InteractionVisualState(self)
        self._hover_index = -1
        self._leaving_index = -1
        self._hover_start = self._leaving_start = self._hover_progress = 0.0
        self.setMouseTracking(True)
        self.currentChanged.connect(self._active_tab_changed)

    def _target(self, index: int) -> QRect:
        tab = self.tabRect(index)
        return (
            QRect(tab.left() + 8, tab.bottom() - 2, max(0, tab.width() - 16), 2)
            if tab.isValid()
            else QRect()
        )

    def _active_tab_changed(self, index: int) -> None:
        self._indicator_index = index
        target = self._target(index)
        motion = AnimationCoordinator.instance()
        if not self._indicator.isValid() or not target.isValid():
            motion.cancel(self, "tab-indicator")
            self._set_indicator(target)
            return
        motion.animate_value(
            self,
            self._indicator,
            target,
            self._set_indicator,
            DURATIONS.tab_indicator,
            channel="tab-indicator",
        )

    def _set_indicator(self, rect: QRect) -> None:
        dirty = self._indicator.united(rect).adjusted(-1, -1, 1, 1)
        self._indicator = rect
        if self.isVisible():
            self.update(dirty)

    def tabLayoutChange(self) -> None:
        super().tabLayoutChange()
        if getattr(self, "_indicator_index", -1) == self.currentIndex() and hasattr(
            self, "_indicator"
        ):
            AnimationCoordinator.instance().cancel(self, "tab-indicator")
            self._set_indicator(self._target(self.currentIndex()))

    def _hover_strength(self, index: int) -> float:
        if index == self._hover_index and index >= 0:
            return self._hover_start + (1 - self._hover_start) * self._hover_progress
        if index == self._leaving_index and index >= 0:
            return self._leaving_start * (1 - self._hover_progress)
        return 0.0

    def _hover_tab(self, index: int) -> None:
        if index == self._hover_index:
            return
        stale = self._leaving_index
        self._hover_start = self._hover_strength(index)
        self._leaving_start = self._hover_strength(self._hover_index)
        self._leaving_index, self._hover_index = self._hover_index, index
        self.update(self.tabRect(stale))
        AnimationCoordinator.instance().animate_value(
            self,
            0.0,
            1.0,
            self._set_hover_progress,
            DURATIONS.hover_enter if index >= 0 else DURATIONS.hover_leave,
            channel="tab-hover",
            retarget=False,
        )

    def _set_hover_progress(self, progress: float) -> None:
        self._hover_progress = progress
        if self.isVisible():
            for index in (self._hover_index, self._leaving_index):
                if index >= 0:
                    self.update(self.tabRect(index))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        index = self.tabAt(event.position().toPoint())
        self._hover_tab(index if index >= 0 and self.isTabEnabled(index) else -1)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover_tab(-1)

    def hideEvent(self, event: QHideEvent) -> None:
        AnimationCoordinator.instance().cancel(self, "tab-hover")
        self._hover_index = self._leaving_index = -1
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._indicator.isValid():
            return
        painter = QPainter(self)
        mode = theme_manager(QApplication.instance()).current_theme
        for index in (self._leaving_index, self._hover_index):
            amount = self._hover_strength(index)
            if amount and self.isTabEnabled(index):
                tint = QColor(_motion_color(mode, "motion_border_hover"))
                tint.setAlphaF(amount * 0.18)
                painter.setBrush(tint)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(self.tabRect(index).adjusted(2, 2, -2, -3), 6, 6)
        painter.fillRect(self._indicator, _motion_color(mode, "motion_focus"))
        focus = self._visual.values[3]
        if focus:
            ring = QColor(_motion_color(mode, "motion_focus"))
            ring.setAlphaF(focus)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.tabRect(self.currentIndex()).adjusted(2, 2, -2, -3), 6, 6)


class VoxTabs(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabBar(_MotionTabBar(self))


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

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event: QHideEvent) -> None:
        self.setRange(0, 4)
        self.setValue(1)
        super().hideEvent(event)


class VoxSettingRow(QWidget):
    def __init__(self, title: str, control: QWidget, *, detail: str = "") -> None:
        super().__init__()
        self.setMinimumHeight(56)
        self._visual = InteractionVisualState(self)
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

    def paintEvent(self, _event: QPaintEvent) -> None:
        hover = self._visual.values[0]
        if not hover:
            return
        mode = theme_manager(QApplication.instance()).current_theme
        shade = QColor(_motion_color(mode, "motion_hover"))
        shade.setAlphaF(0.55 * hover)
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setBrush(shade)
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

    def paintEvent(self, _event: QPaintEvent) -> None:
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

    def paintEvent(self, _event: QPaintEvent) -> None:
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
        self.card = VoxCard(self, emphasis=True)
        self.card.content_layout.setContentsMargins(14, 10, 12, 10)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._offset = 0
        self._timer: QTimer | None = None
        self._remaining_ms = 0
        self._lifetime_ms = 0
        self._pointer_inside = False

    def set_timer(self, timer: QTimer) -> None:
        self._timer = timer
        self._lifetime_ms = timer.interval()
        self.restart_timeout()

    def restart_timeout(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._remaining_ms = self._lifetime_ms
            self._resume_timeout()

    def stop_timeout(self) -> None:
        self._remaining_ms = 0
        if self._timer is not None:
            self._timer.stop()

    def _pause_timeout(self) -> None:
        if self._timer is not None and self._timer.isActive():
            self._remaining_ms = max(1, self._timer.remainingTime())
            self._timer.stop()

    def _resume_timeout(self) -> None:
        if (
            self._timer is not None
            and self._remaining_ms
            and self.isVisible()
            and not self._pointer_inside
        ):
            self._timer.start(self._remaining_ms)

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._pointer_inside = True
        self._pause_timeout()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._pointer_inside = False
        self._resume_timeout()

    def hideEvent(self, event: QHideEvent) -> None:
        self._pause_timeout()
        self._pointer_inside = False
        super().hideEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._resume_timeout()

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
        self.card.move(0, self._offset)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.card.resize(self.size())
        self.card.move(0, self._offset)


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
            label = slot.card.findChild(QLabel, "toastText")
            if label is not None:
                label.setText(f"{heading} · {text}")
            more = slot.card.findChild(QPushButton, "toastDetails")
            if more is not None:
                more.setVisible(bool(details))
            if key in self.timers:
                slot.restart_timeout()
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
                slot.set_timer(timer)
            self.show()
            slot.show()
            motion.animate_value(slot, 10, 0, slot.set_offset, motion.TOAST_ENTER, channel="slide")
            motion.animate_opacity(card, 0.0, 1.0, motion.TOAST_ENTER)
        self.reposition()

    def dismiss(self, key: str, *, immediate: bool = False) -> None:
        slot = self.items.get(key)
        if slot is None:
            return
        slot.stop_timeout()
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
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(0, parent.width() - self.width() - 16), 72)
        self.raise_()

    def _show_details(self, title: str, text: str, details: str) -> None:
        dialog = VoxMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDetailedText(details)
        dialog.exec()


class VoxDialog(QDialog):
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        motion = AnimationCoordinator.instance()
        motion.animate_opacity(self, 0.70, 1.0, motion.MODAL)


class VoxMessageBox(QMessageBox):
    def showEvent(self, event: QShowEvent) -> None:
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
