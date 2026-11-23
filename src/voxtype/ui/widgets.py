"""Reusable presentation-only Qt widgets."""

from __future__ import annotations

import math
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6


def rms_to_meter(value: float) -> float:
    """Map normalized PCM RMS to a perceptually useful dBFS meter."""

    rms = max(0.0, float(value))
    if rms <= 1e-8:
        return 0.0
    dbfs = 20.0 * math.log10(rms)
    return min(1.0, max(0.0, (dbfs + 75.0) / 69.0))


if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import Property, QRect, QRectF, QSize, Qt, Signal
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    from .motion import DURATIONS, AnimationCoordinator
    from .theme import LIGHT_PALETTE, SPACING, theme_manager

    class Card(QFrame):
        def __init__(self, parent: QWidget | None = None, *, spacing: int = 10) -> None:
            super().__init__(parent)
            self.setProperty("card", True)
            self.setFrameShape(QFrame.Shape.NoFrame)
            self.content_layout = QVBoxLayout(self)
            self.content_layout.setContentsMargins(
                SPACING.space_5,
                SPACING.space_5,
                SPACING.space_5,
                SPACING.space_5,
            )
            self.content_layout.setSpacing(spacing)

    class ElidedLabel(QLabel):
        """Single-line label that preserves long values in its tooltip."""

        def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
            super().__init__("", parent)
            self._full_text = str(text)
            self.setWordWrap(False)
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self._update_elision()

        def setText(self, text: str) -> None:
            self._full_text = str(text)
            self._update_elision()

        def full_text(self) -> str:
            return self._full_text

        def resizeEvent(self, event: Any) -> None:
            super().resizeEvent(event)
            self._update_elision()

        def _update_elision(self) -> None:
            available = max(0, self.contentsRect().width())
            visible = self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
            super().setText(visible)
            self.setAccessibleName(self._full_text)
            self.setToolTip(self._full_text if visible != self._full_text else "")

    class AudioLevelMeter(QWidget):
        level_changed = Signal(float)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._level = 0.0
            self._clipping = 0.0
            self.setMinimumSize(120, 14)
            self.setMaximumHeight(18)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setAccessibleName("Уровень микрофона")

        def sizeHint(self) -> QSize:
            return QSize(180, 14)

        def level(self) -> float:
            return self._level

        def set_level(self, value: float, clipping_percentage: float | None = None) -> None:
            normalized = min(1.0, max(0.0, float(value)))
            clipping = (
                self._clipping
                if clipping_percentage is None
                else min(100.0, max(0.0, float(clipping_percentage)))
            )
            changed = normalized != self._level
            self._level = normalized
            self._clipping = clipping
            self.setAccessibleDescription(
                f"Уровень {round(normalized * 100)} процентов, клиппинг {clipping:.1f} процента"
            )
            self.update()
            if changed:
                self.level_changed.emit(normalized)

        value = Property(float, level, set_level)

        def paintEvent(self, event: Any) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(LIGHT_PALETTE.border))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            filled = QRectF(rect)
            filled.setWidth(rect.width() * self._level)
            if self._clipping > 0.1:
                color = QColor(LIGHT_PALETTE.error)
            elif self._level >= 0.90:
                color = QColor(LIGHT_PALETTE.warning)
            else:
                color = QColor(LIGHT_PALETTE.success)
            painter.setBrush(color)
            if filled.width() > 0:
                painter.drawRoundedRect(filled, rect.height() / 2, rect.height() / 2)

    class StatusDot(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._color = QColor(LIGHT_PALETTE.success)
            self.setFixedSize(14, 14)
            self.setAccessibleName("Индикатор состояния")

        def set_color(self, color: str) -> None:
            self._color = QColor(color)
            self.update()

        def paintEvent(self, event: Any) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(self._color.lighter(155), 2))
            painter.setBrush(self._color)
            painter.drawEllipse(self.rect().adjusted(2, 2, -2, -2))

    class SegmentedLanguageControl(QFrame):
        language_changed = Signal(str)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setProperty("segmented", True)
            self.setAccessibleName("Язык диктовки")
            self._indicator = QRect()
            layout = QHBoxLayout(self)
            layout.setContentsMargins(3, 3, 3, 3)
            layout.setSpacing(2)
            self.group = QButtonGroup(self)
            self.group.setExclusive(True)
            self.ru_button = self._button("RU", "ru")
            self.en_button = self._button("EN", "en")
            layout.addWidget(self.ru_button)
            layout.addWidget(self.en_button)
            self.ru_button.setChecked(True)
            self.group.buttonClicked.connect(self._emit_language)
            self.group.buttonToggled.connect(self._update_selection)

        def _button(self, text: str, language: str) -> QPushButton:
            from .obsidian import VoxButton

            button = VoxButton(text, self, variant="segment")
            button.setMinimumHeight(30)
            button.setCheckable(True)
            button.setProperty("groupIndicator", True)
            button.setProperty("language", language)
            button.setAccessibleName(f"Язык {text}")
            button.setMinimumWidth(54)
            self.group.addButton(button)
            return button

        def _update_selection(self, button: QPushButton, checked: bool) -> None:
            if checked:
                AnimationCoordinator.instance().animate_value(
                    self,
                    self._indicator,
                    button.geometry(),
                    self._set_indicator,
                    DURATIONS.selection,
                    channel="language-indicator",
                )

        def _set_indicator(self, rect: QRect) -> None:
            dirty = self._indicator.united(rect)
            self._indicator = rect
            if self.isVisible():
                self.update(dirty)

        def resizeEvent(self, event: Any) -> None:
            super().resizeEvent(event)
            self._align_indicator()

        def showEvent(self, event: Any) -> None:
            super().showEvent(event)
            self._align_indicator()

        def _align_indicator(self) -> None:
            selected = self.group.checkedButton()
            if selected is not None:
                AnimationCoordinator.instance().cancel(self, "language-indicator")
                self._set_indicator(selected.geometry())

        def paintEvent(self, event: Any) -> None:
            super().paintEvent(event)
            if self._indicator.isValid():
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                manager = theme_manager(QApplication.instance())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(manager.get_token("motion_selected")))
                painter.drawRoundedRect(QRectF(self._indicator), 8, 8)

        def _emit_language(self, button: QPushButton) -> None:
            self.language_changed.emit(str(button.property("language")))

        def set_language(self, language: str) -> None:
            target = self.en_button if str(language).lower().startswith("en") else self.ru_button
            target.setChecked(True)

        def language(self) -> str:
            checked = self.group.checkedButton()
            return str(checked.property("language")) if checked is not None else "ru"

    class MetricTile(Card):
        def __init__(self, title: str, value: str = "—", parent: QWidget | None = None) -> None:
            super().__init__(parent, spacing=4)
            self.setMinimumHeight(82)
            self.title_label = QLabel(title, self)
            self.title_label.setProperty("caption", True)
            self.value_label = ElidedLabel(value, self)
            self.value_label.setProperty("sectionHeading", True)
            self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.content_layout.addWidget(self.title_label)
            self.content_layout.addWidget(self.value_label)

        def set_value(self, value: str, tooltip: str = "") -> None:
            self.value_label.setText(value)
            self.value_label.setToolTip(tooltip or self.value_label.toolTip())

else:

    class _UnavailableWidget:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()

    class Card(_UnavailableWidget):  # type: ignore[no-redef]
        """Import guard that fails clearly at construction."""

    class AudioLevelMeter(_UnavailableWidget):  # type: ignore[no-redef]
        """Import guard that fails clearly at construction."""

    class ElidedLabel(_UnavailableWidget):  # type: ignore[no-redef]
        """Import guard that fails clearly at construction."""

    class StatusDot(_UnavailableWidget):  # type: ignore[no-redef]
        """Import guard that fails clearly at construction."""

    class SegmentedLanguageControl(_UnavailableWidget):  # type: ignore[no-redef]
        """Import guard that fails clearly at construction."""

    class MetricTile(_UnavailableWidget):  # type: ignore[no-redef]
        """Import guard that fails clearly at construction."""
