"""Event-driven motion shared by native Qt widgets."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QSettings,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFocusEvent
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget


@dataclass(frozen=True, slots=True)
class MotionDurations:
    hover_enter: int = 140
    hover_leave: int = 170
    press_in: int = 75
    press_out: int = 135
    focus_in: int = 140
    focus_out: int = 160
    selection: int = 170
    toggle: int = 170
    border: int = 150
    icon: int = 140
    tooltip_delay: int = 600
    sidebar_selection: int = 180
    tab_indicator: int = 190
    card_emphasis: int = 160
    toast_enter: int = 200
    toast_exit: int = 160


DURATIONS = MotionDurations()
OUT = QEasingCurve.Type.OutCubic
PRESS_IN = QEasingCurve.Type.OutQuad
TOGGLE = QEasingCurve.Type.InOutCubic
_OUT_CURVE = QEasingCurve(OUT)
_PRESS_CURVE = QEasingCurve(PRESS_IN)


class InteractionState(StrEnum):
    NORMAL = "normal"
    HOVERED = "hovered"
    PRESSED = "pressed"
    SELECTED = "selected"
    FOCUSED = "focused"
    DISABLED = "disabled"


@dataclass(slots=True)
class InteractionFlags:
    hovered: bool = False
    pressed: bool = False
    selected: bool = False
    focused: bool = False
    enabled: bool = True


def blend(start: QColor, end: QColor, progress: float) -> QColor:
    return QColor.fromRgbF(
        start.redF() + (end.redF() - start.redF()) * progress,
        start.greenF() + (end.greenF() - start.greenF()) * progress,
        start.blueF() + (end.blueF() - start.blueF()) * progress,
        start.alphaF() + (end.alphaF() - start.alphaF()) * progress,
    )


class InteractionVisualState(QObject):
    """Retarget four paint-only channels with one reusable timeline."""

    def __init__(
        self,
        widget: QWidget,
        *,
        mouse_focus: bool = False,
        pointer: bool = False,
        hover_duration: int = DURATIONS.hover_enter,
    ) -> None:
        super().__init__(widget)
        self.widget = widget
        self.mouse_focus = mouse_focus
        self.pointer = pointer
        self.hover_duration = hover_duration
        self.flags = InteractionFlags(enabled=widget.isEnabled())
        self.values: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0)
        self._from: tuple[float, ...] = self.values
        self._to: tuple[float, ...] = self.values
        self._timing: tuple[tuple[int, QEasingCurve], ...] = ()
        self._animation: QVariantAnimation | None = None
        self._motion = AnimationCoordinator.instance()
        self._motion.preference_updated.connect(self._preference_changed)
        widget.installEventFilter(self)
        self._sync_cursor()

    def _sync_cursor(self) -> None:
        if self.pointer:
            self.widget.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self.flags.enabled
                else Qt.CursorShape.ArrowCursor
            )

    def resolve_visual_target(self) -> tuple[InteractionState, bool]:
        flags = self.flags
        if not flags.enabled:
            return InteractionState.DISABLED, False
        if flags.pressed:
            return InteractionState.PRESSED, flags.focused
        if flags.selected:
            return InteractionState.SELECTED, flags.focused
        if flags.hovered:
            return InteractionState.HOVERED, flags.focused
        return (
            InteractionState.FOCUSED if flags.focused else InteractionState.NORMAL
        ), flags.focused

    def set_pressed(self, pressed: bool) -> None:
        flags = self.flags
        flags.pressed = pressed and flags.enabled
        self.transition_to_target()

    def set_selected(self, selected: bool) -> None:
        self.flags.selected = selected
        self.transition_to_target()

    def transition_to_target(self, *, immediate: bool = False) -> None:
        flags = self.flags
        state, focused = self.resolve_visual_target()
        enabled = state is not InteractionState.DISABLED
        target = (
            float(enabled and flags.hovered),
            float(enabled and flags.pressed),
            float(flags.selected),
            float(focused),
        )
        if immediate or not self.widget.isVisible() or self._motion.reduced_motion_enabled():
            if self._animation is not None:
                self._animation.stop()
            self._to = target
            self._apply(target)
            return
        if target == self._to:
            return
        durations = (
            self.hover_duration if target[0] else DURATIONS.hover_leave,
            DURATIONS.press_in if target[1] else DURATIONS.press_out,
            DURATIONS.selection,
            DURATIONS.focus_in if target[3] else DURATIONS.focus_out,
        )
        animation = self._animation
        running = animation is not None and animation.state() == QAbstractAnimation.State.Running
        elapsed = animation.currentTime() if animation is not None else 0
        previous = self._to
        old_timing = self._timing
        self._from, self._to = self.values, target
        timing: list[tuple[int, QEasingCurve]] = []
        for i, (duration, before, after) in enumerate(
            zip(durations, self._from, target, strict=True)
        ):
            if before == after:
                duration = 0
            elif running and after == previous[i]:
                duration = max(1, old_timing[i][0] - elapsed)
            timing.append((duration, _PRESS_CURVE if i == 1 and after else _OUT_CURVE))
        self._timing = tuple(timing)
        if animation is not None:
            animation.stop()
        duration = max(ms for ms, _curve in self._timing)
        if not duration:
            return
        if self._animation is None:
            self._animation = QVariantAnimation(self)
            self._animation.valueChanged.connect(self._tick)
            self._animation.finished.connect(self._finish)
        self._animation.setDuration(duration)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(float(duration))
        self._animation.start()

    def _apply(self, values: tuple[float, ...]) -> None:
        if values != self.values:
            self.values = values
            if self.widget.isVisible():
                self.widget.update()

    def _tick(self, elapsed: float) -> None:
        if (
            self._animation is not None
            and self._animation.state() == QAbstractAnimation.State.Running
        ):
            channels = zip(self._from, self._to, self._timing, strict=True)
            self._apply(
                tuple(
                    start + (end - start) * curve.valueForProgress(min(1.0, elapsed / duration))
                    if duration
                    else end
                    for start, end, (duration, curve) in channels
                )
            )

    def _finish(self) -> None:
        self._apply(self._to)

    def _preference_changed(self) -> None:
        if self._motion.reduced_motion_enabled():
            self.transition_to_target(immediate=True)

    def eventFilter(self, _owner: QObject, event: QEvent) -> bool:
        kind = event.type()
        flags = self.flags
        immediate = kind in {
            QEvent.Type.EnabledChange,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.WindowDeactivate,
        }
        if kind in {QEvent.Type.Enter, QEvent.Type.Leave}:
            flags.hovered = kind == QEvent.Type.Enter and flags.enabled
        elif kind in {QEvent.Type.FocusIn, QEvent.Type.FocusOut}:
            # Mouse activation uses press feedback. Tab navigation keeps a ring.
            flags.focused = (
                kind == QEvent.Type.FocusIn
                and isinstance(event, QFocusEvent)
                and (self.mouse_focus or event.reason() != Qt.FocusReason.MouseFocusReason)
            )
        elif immediate:
            flags.enabled = self.widget.isEnabled()
            if kind == QEvent.Type.EnabledChange:
                self._sync_cursor()
            flags.hovered = (
                flags.enabled
                and kind not in {QEvent.Type.Hide, QEvent.Type.WindowDeactivate}
                and self.widget.underMouse()
            )
            flags.pressed = False
            if not flags.enabled or kind == QEvent.Type.Hide:
                flags.focused = False
        else:
            return False
        self.transition_to_target(immediate=immediate)
        return False


class MotionPreference(StrEnum):
    SYSTEM = "system"
    FULL = "full"
    REDUCED = "reduced"

    @classmethod
    def load(cls, settings: QSettings) -> MotionPreference:
        saved = settings.value("ui/motionPreference")
        if saved is None:
            return (
                cls.REDUCED if settings.value("ui/reduceMotion", False, type=bool) else cls.SYSTEM
            )
        try:
            return cls(saved)
        except ValueError:
            return cls.SYSTEM


@dataclass(slots=True)
class _Transition:
    animation: QVariantAnimation
    end: Any
    apply: Callable[[Any], None]
    cleanup: Callable[[], None] | None
    finished: Callable[[], None] | None


class AnimationCoordinator(QObject):
    preference_updated = Signal()
    HOVER = DURATIONS.hover_enter
    PRESS = DURATIONS.press_in
    PAGE = 160
    SIDEBAR = 180
    SELECTION = DURATIONS.selection
    TOAST_ENTER = DURATIONS.toast_enter
    TOAST_EXIT = DURATIONS.toast_exit
    MODAL = 180
    PALETTE = 140
    OVERLAY_ENTER = 140
    OVERLAY_EXIT = 110
    SWITCH = DURATIONS.toggle
    RIPPLE = 240

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self._application = application
        self._running: dict[int, dict[str, _Transition]] = {}
        self._animations: dict[int, dict[str, QVariantAnimation]] = {}
        self._observed: set[int] = set()
        self._system_motion = self._system_allows_motion()
        application.applicationStateChanged.connect(self._refresh_system_preference)

    @classmethod
    def instance(cls) -> AnimationCoordinator:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("VoxType motion requires QApplication")
        coordinator = getattr(application, "_voxtype_motion", None)
        if not isinstance(coordinator, cls):
            coordinator = cls(application)
            application.__dict__["_voxtype_motion"] = coordinator
        return coordinator

    @staticmethod
    def _system_allows_motion() -> bool:
        if sys.platform == "win32":
            enabled = ctypes.c_int()
            try:
                if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                    return bool(enabled.value)
            except (AttributeError, OSError):
                return True
        return True

    def _refresh_system_preference(self, _state: object) -> None:
        self._system_motion = self._system_allows_motion()
        self.preference_changed()

    def preference_changed(self) -> None:
        if self.reduced_motion_enabled():
            self._settle_all()
        self.preference_updated.emit()

    def reduced_motion_enabled(self) -> bool:
        preference = self._application.property("voxtypeMotionPreference")
        if preference == MotionPreference.FULL:
            return False
        return (
            preference == MotionPreference.REDUCED
            or not self._system_motion
            or bool(self._application.property("voxtypeEcoMotion"))
        )

    def eventFilter(self, owner: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Hide:
            self.cancel_all(owner, settle=True)
        return False

    def _settle_all(self) -> None:
        for key in tuple(self._running):
            for channel in tuple(self._running.get(key, {})):
                self._cancel(key, channel, settle=True)

    def _observe(self, owner: QObject) -> int:
        key = id(owner)
        if key not in self._observed:
            self._observed.add(key)
            owner.installEventFilter(self)
            owner.destroyed.connect(lambda *_args: self._forget(key))
        return key

    def _forget(self, key: int) -> None:
        self._running.pop(key, None)
        self._animations.pop(key, None)
        self._observed.discard(key)

    def _take(self, key: int, channel: str) -> _Transition | None:
        entry = self._running.get(key)
        if entry is None:
            return None
        transition = entry.pop(channel, None)
        if not entry:
            self._running.pop(key, None)
        return transition

    def _cancel(self, key: int, channel: str, *, settle: bool = False) -> None:
        transition = self._take(key, channel)
        if transition is None:
            return
        transition.animation.stop()
        if settle:
            transition.apply(transition.end)
        if transition.cleanup is not None:
            transition.cleanup()
        if settle and transition.finished is not None:
            transition.finished()

    def cancel(self, owner: QObject, channel: str = "value", *, settle: bool = False) -> None:
        self._cancel(id(owner), channel, settle=settle)

    def cancel_all(self, owner: QObject, *, settle: bool = False) -> None:
        key = id(owner)
        for channel in tuple(self._running.get(key, {})):
            self._cancel(key, channel, settle=settle)

    def animate_value(
        self,
        owner: QObject,
        start: Any,
        end: Any,
        callback: Callable[[Any], None],
        duration: int,
        *,
        channel: str = "value",
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
        finished: Callable[[], None] | None = None,
        cleanup: Callable[[], None] | None = None,
        retarget: bool = True,
    ) -> None:
        key = id(owner)
        previous = self._take(key, channel)
        if previous is not None:
            if retarget:
                start = previous.animation.currentValue()
            previous.animation.stop()
            if previous.cleanup is not None and cleanup is None:
                previous.cleanup()
        if (
            duration <= 0
            or start == end
            or self.reduced_motion_enabled()
            or (isinstance(owner, QWidget) and not owner.isVisible())
        ):
            callback(end)
            if cleanup is not None:
                cleanup()
            if finished is not None:
                finished()
            return
        self._observe(owner)
        animations = self._animations.setdefault(key, {})
        animation = animations.get(channel)
        if animation is None:
            animation = animations[channel] = QVariantAnimation(owner)
            animation.valueChanged.connect(lambda value: self._apply_frame(key, channel, value))
            animation.finished.connect(lambda: self._finish_channel(key, channel))
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(easing)

        self._running.setdefault(key, {})[channel] = _Transition(
            animation, end, callback, cleanup, finished
        )
        callback(start)
        animation.start()

    def _apply_frame(self, key: int, channel: str, value: Any) -> None:
        transition = self._running.get(key, {}).get(channel)
        if transition is not None:
            transition.apply(value)

    def _finish_channel(self, key: int, channel: str) -> None:
        transition = self._take(key, channel)
        if transition is not None:
            transition.apply(transition.end)
            if transition.cleanup is not None:
                transition.cleanup()
            if transition.finished is not None:
                transition.finished()

    def animate_color(
        self,
        widget: QWidget,
        property_name: str,
        start: QColor,
        end: QColor,
        duration: int,
        *,
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    ) -> None:
        def apply(value: QColor) -> None:
            setattr(widget, property_name, value)
            widget.update()

        self.animate_value(
            widget, start, end, apply, duration, channel=property_name, easing=easing
        )

    def animate_opacity(
        self,
        widget: QWidget,
        start: float,
        end: float,
        duration: int,
        *,
        finished: Callable[[], None] | None = None,
    ) -> None:
        if self.reduced_motion_enabled() or not widget.isVisible():
            self.cancel(widget, "opacity")
            if finished is not None:
                finished()
            return
        existing = widget.graphicsEffect()
        if existing is not None and not (
            isinstance(existing, QGraphicsOpacityEffect) and existing.property("voxtypeOpacity")
        ):
            if finished is not None:
                finished()
            return
        if isinstance(existing, QGraphicsOpacityEffect):
            effect = existing
        else:
            effect = QGraphicsOpacityEffect(widget)
            effect.setProperty("voxtypeOpacity", True)
            widget.setGraphicsEffect(effect)

        def clear() -> None:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)  # type: ignore[arg-type]  # Qt accepts nullptr.

        self.animate_value(
            widget,
            start,
            end,
            effect.setOpacity,
            duration,
            channel="opacity",
            finished=finished,
            cleanup=clear,
        )
