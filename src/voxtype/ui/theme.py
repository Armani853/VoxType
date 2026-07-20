"""VoxType visual system built on native Qt widgets."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6


class ThemeMode(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


LIGHT_COLORS = {
    "window": "#F4F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#EEF2F7",
    "text": "#172033",
    "muted": "#657089",
    "border": "#DDE3ED",
    "accent": "#5B5FEF",
    "accent_hover": "#4C50DB",
    "accent_soft": "#ECECFF",
    "success": "#1A9B67",
    "warning": "#D98B16",
    "danger": "#D34A55",
}

DARK_COLORS = {
    "window": "#10131B",
    "surface": "#191E2A",
    "surface_alt": "#222938",
    "text": "#F2F5FA",
    "muted": "#A7B0C2",
    "border": "#30394A",
    "accent": "#7B7FF6",
    "accent_hover": "#9093FF",
    "accent_soft": "#292B58",
    "success": "#45C691",
    "warning": "#F0AE42",
    "danger": "#EF6871",
}


def stylesheet(mode: ThemeMode | str = ThemeMode.LIGHT) -> str:
    resolved = ThemeMode(mode) if str(mode) in ThemeMode._value2member_map_ else ThemeMode.LIGHT
    colors = DARK_COLORS if resolved is ThemeMode.DARK else LIGHT_COLORS
    return f"""
    QWidget {{
        color: {colors["text"]};
        font-family: "Segoe UI Variable", "Segoe UI";
        font-size: 10pt;
    }}
    QMainWindow, QDialog, QWizard {{ background: {colors["window"]}; }}
    QLabel[muted="true"] {{ color: {colors["muted"]}; }}
    QLabel[heading="true"] {{ font-size: 18pt; font-weight: 650; }}
    QLabel[sectionHeading="true"] {{ font-size: 12pt; font-weight: 650; }}
    QFrame[card="true"] {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 14px;
    }}
    QPushButton {{
        min-height: 34px;
        padding: 0 14px;
        border: 1px solid {colors["border"]};
        border-radius: 9px;
        background: {colors["surface"]};
    }}
    QPushButton:hover {{ background: {colors["surface_alt"]}; }}
    QPushButton:pressed {{ background: {colors["border"]}; }}
    QPushButton:disabled {{ color: {colors["muted"]}; background: {colors["surface_alt"]}; }}
    QPushButton[primary="true"] {{
        color: white;
        background: {colors["accent"]};
        border-color: {colors["accent"]};
        min-height: 52px;
        border-radius: 13px;
        font-size: 12pt;
        font-weight: 650;
    }}
    QPushButton[primary="true"]:hover {{ background: {colors["accent_hover"]}; }}
    QPushButton[danger="true"] {{ color: white; background: {colors["danger"]}; border-color: {colors["danger"]}; }}
    QPushButton[flat="true"] {{ border-color: transparent; background: transparent; }}
    QPushButton[segment="true"] {{
        min-height: 30px;
        padding: 0 18px;
        border-radius: 8px;
        background: transparent;
        border-color: transparent;
        color: {colors["muted"]};
        font-weight: 600;
    }}
    QPushButton[segment="true"]:checked {{
        color: {colors["accent"]};
        background: {colors["accent_soft"]};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        padding: 7px 9px;
        selection-background-color: {colors["accent"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {colors["accent"]}; }}
    QComboBox {{ min-height: 24px; }}
    QComboBox QAbstractItemView {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        selection-background-color: {colors["accent_soft"]};
        selection-color: {colors["text"]};
    }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QSlider::groove:horizontal {{ height: 5px; border-radius: 2px; background: {colors["border"]}; }}
    QSlider::sub-page:horizontal {{ background: {colors["accent"]}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 16px; margin: -6px 0; border-radius: 8px; background: {colors["accent"]};
    }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: {colors["surface"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
        outline: none;
    }}
    QListWidget::item, QTreeWidget::item {{ padding: 8px; border-radius: 7px; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background: {colors["accent_soft"]}; color: {colors["text"]};
    }}
    QHeaderView::section {{
        background: {colors["surface_alt"]}; color: {colors["muted"]};
        padding: 7px; border: none; border-bottom: 1px solid {colors["border"]};
    }}
    QProgressBar {{
        background: {colors["surface_alt"]}; border: none; border-radius: 5px;
        min-height: 10px; max-height: 10px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {colors["accent"]}; border-radius: 5px; }}
    QTabWidget::pane {{ border: 1px solid {colors["border"]}; border-radius: 10px; background: {colors["surface"]}; }}
    QTabBar::tab {{ padding: 9px 14px; color: {colors["muted"]}; }}
    QTabBar::tab:selected {{ color: {colors["accent"]}; font-weight: 600; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QToolTip {{ color: {colors["text"]}; background: {colors["surface"]}; border: 1px solid {colors["border"]}; padding: 5px; }}
    """


if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap

    def resolved_theme(app: Any, mode: ThemeMode | str) -> ThemeMode:
        requested = (
            ThemeMode(mode) if str(mode) in ThemeMode._value2member_map_ else ThemeMode.SYSTEM
        )
        if requested is not ThemeMode.SYSTEM:
            return requested
        lightness = app.palette().color(QPalette.ColorRole.Window).lightness()
        return ThemeMode.DARK if lightness < 128 else ThemeMode.LIGHT

    def apply_theme(app: Any, mode: ThemeMode | str = ThemeMode.SYSTEM) -> ThemeMode:
        resolved = resolved_theme(app, mode)
        app.setProperty("voxtypeTheme", resolved.value)
        app.setStyleSheet(stylesheet(resolved))
        return resolved

    def create_app_icon(size: int = 64, color: str = "#5B5FEF") -> Any:
        """Create a sharp DPI-independent microphone icon without asset I/O."""

        side = max(16, int(size))
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        radius = side * 0.22
        painter.drawRoundedRect(1, 1, side - 2, side - 2, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor("white"), max(1.6, side * 0.075))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        mic_width = side * 0.26
        mic_height = side * 0.34
        mic_left = (side - mic_width) / 2
        mic_top = side * 0.22
        painter.drawRoundedRect(
            int(mic_left),
            int(mic_top),
            int(mic_width),
            int(mic_height),
            mic_width / 2,
            mic_width / 2,
        )
        arc_left = side * 0.30
        arc_top = side * 0.35
        painter.drawArc(
            int(arc_left),
            int(arc_top),
            int(side * 0.40),
            int(side * 0.34),
            180 * 16,
            180 * 16,
        )
        painter.drawLine(int(side / 2), int(side * 0.69), int(side / 2), int(side * 0.79))
        painter.drawLine(int(side * 0.39), int(side * 0.79), int(side * 0.61), int(side * 0.79))
        painter.end()
        return QIcon(pixmap)

else:

    def resolved_theme(app: Any, mode: ThemeMode | str) -> ThemeMode:
        require_pyside6()
        return ThemeMode.SYSTEM

    def apply_theme(app: Any, mode: ThemeMode | str = ThemeMode.SYSTEM) -> ThemeMode:
        require_pyside6()
        return ThemeMode.SYSTEM

    def create_app_icon(size: int = 64, color: str = "#5B5FEF") -> Any:
        require_pyside6()
        return None
