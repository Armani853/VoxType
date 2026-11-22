"""Event-driven motion shared by native Qt widgets."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QSettings, QVariantAnimation, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget


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
    HOVER = 120
    PRESS = 80
    PAGE = 160
    SIDEBAR = 180
    SELECTION = 160
    TOAST_ENTER = 180
    TOAST_EXIT = 140
    MODAL = 180
    PALETTE = 140
    OVERLAY_ENTER = 140
    OVERLAY_EXIT = 110
    SWITCH = 150
    RIPPLE = 240

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self._application = application
        self._running: dict[int, dict[str, _Transition]] = {}
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
        transition.animation.deleteLater()
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
            previous.animation.deleteLater()
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
        animation = QVariantAnimation(owner)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(easing)

        def done() -> None:
            current = self._running.get(key, {}).get(channel)
            if current is None or current.animation is not animation:
                return
            self._take(key, channel)
            animation.deleteLater()
            if cleanup is not None:
                cleanup()
            if finished is not None:
                finished()

        self._running.setdefault(key, {})[channel] = _Transition(
            animation, end, callback, cleanup, finished
        )
        animation.valueChanged.connect(callback)
        animation.finished.connect(done)
        callback(start)
        animation.start()

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
