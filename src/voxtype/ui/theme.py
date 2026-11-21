"""VoxType visual system built on native Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6


class ThemeMode(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class SemanticPalette:
    background: str
    surface: str
    surface_subtle: str
    text_primary: str
    text_secondary: str
    text_tertiary: str
    border: str
    border_strong: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_subtle: str
    success: str
    success_subtle: str
    warning: str
    warning_subtle: str
    error: str
    error_subtle: str
    info: str
    info_subtle: str
    focus_ring: str
    disabled_text: str
    disabled_surface: str


@dataclass(frozen=True, slots=True)
class SpacingTokens:
    space_1: int = 4
    space_2: int = 8
    space_3: int = 12
    space_4: int = 16
    space_5: int = 20
    space_6: int = 24
    space_8: int = 32
    space_10: int = 40


@dataclass(frozen=True, slots=True)
class RadiusTokens:
    radius_sm: int = 8
    radius_md: int = 8
    radius_lg: int = 12


@dataclass(frozen=True, slots=True)
class TypographyTokens:
    body_pt: int = 10
    secondary_pt: int = 9
    caption_pt: int = 8
    card_title_pt: int = 12
    window_title_pt: int = 20


DARK = dict(
    canvas="#101116",
    sidebar="#14161D",
    surface="#191B23",
    surface_hover="#222530",
    surface_raised="#252835",
    border="#303442",
    border_subtle="#262A35",
    text_primary="#F2F3F8",
    text_secondary="#A0A5B8",
    text_muted="#73798D",
    accent="#818CF8",
    accent_hover="#9BA3FF",
    accent_pressed="#6975E5",
    accent_soft="#292D4D",
    success="#34D399",
    warning="#FBBF24",
    danger="#FB7185",
)
LIGHT = dict(
    canvas="#F7F8FC",
    sidebar="#F0F2F7",
    surface="#FFFFFF",
    surface_hover="#F1F3F9",
    surface_raised="#E9ECF5",
    border="#DCE0EB",
    border_subtle="#E9ECF2",
    text_primary="#181B27",
    text_secondary="#626A7C",
    text_muted="#828A9A",
    accent="#5B61D6",
    accent_hover="#4F55CB",
    accent_pressed="#4048B4",
    accent_soft="#E9EAFE",
    success="#16845E",
    warning="#A66A08",
    danger="#D34259",
)


def _semantic(tokens: dict[str, str], *, dark: bool) -> SemanticPalette:
    t = tokens
    return SemanticPalette(
        t["canvas"],
        t["surface"],
        t["surface_hover"],
        t["text_primary"],
        t["text_secondary"],
        t["text_muted"],
        t["border_subtle"],
        t["border"],
        t["accent"],
        t["accent_hover"],
        t["accent_pressed"],
        t["accent_soft"],
        t["success"],
        "#16342D" if dark else "#E7F6EF",
        t["warning"],
        "#392F20" if dark else "#FFF4DE",
        t["danger"],
        "#39252F" if dark else "#FFEBEF",
        t["accent"],
        t["accent_soft"],
        t["accent"],
        t["text_muted"],
        t["surface_raised"],
    )


LIGHT_PALETTE = _semantic(LIGHT, dark=False)
DARK_PALETTE = _semantic(DARK, dark=True)

OVERLAY_PALETTE = {
    "background": "#191B23",
    "surface": "#222938",
    "surface_hover": "#354055",
    "text_primary": "#F7F8FB",
    "text_secondary": "#B8C0D0",
    "text_tertiary": "#929DB2",
    "border": "rgba(255, 255, 255, 36)",
    "primary": "#6C70F4",
    "primary_hover": "#7C80FA",
    "primary_subtle": "#30335C",
    "success": "#3BC68B",
    "warning": "#E5A43B",
    "error": "#D95360",
    "error_hover": "#E36570",
    "result_surface": "#F8FAFD",
    "result_text": "#172033",
    "result_border": "#DCE3ED",
}

SPACING = SpacingTokens()
RADII = RadiusTokens()
TYPOGRAPHY = TypographyTokens()

# Compatibility aliases for callers that used the original public names.
LIGHT_COLORS = LIGHT_PALETTE
DARK_COLORS = DARK_PALETTE


def palette(mode: ThemeMode | str = ThemeMode.LIGHT) -> SemanticPalette:
    resolved = ThemeMode(mode) if str(mode) in ThemeMode._value2member_map_ else ThemeMode.LIGHT
    return DARK_PALETTE if resolved is ThemeMode.DARK else LIGHT_PALETTE


def state_color(state: str, mode: ThemeMode | str | None = None) -> str:
    if mode is None and PYSIDE6_AVAILABLE:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            mode = app.property("voxtypeTheme")
    colors = palette(mode or ThemeMode.LIGHT)
    return {
        "neutral": colors.text_tertiary,
        "primary": colors.primary,
        "success": colors.success,
        "warning": colors.warning,
        "error": colors.error,
        "info": colors.info,
    }.get(state, colors.text_tertiary)


@lru_cache(maxsize=4)
def stylesheet(mode: ThemeMode | str = ThemeMode.LIGHT) -> str:
    colors = palette(mode)
    tokens = DARK if colors is DARK_PALETTE else LIGHT
    on_accent = tokens["canvas"] if tokens is DARK else "#FFFFFF"
    return f"""
    QMainWindow, QDialog, QWizard, QWizardPage {{ background: {colors.background}; }}
    QLabel[muted="true"] {{ color: {colors.text_secondary}; }}
    QLabel[tertiary="true"] {{ color: {colors.text_secondary}; }}
    QLabel[heading="true"] {{
        color: {colors.text_primary};

    }}
    QLabel[sectionHeading="true"] {{
        color: {colors.text_primary};

    }}
    QLabel[caption="true"] {{
        color: {colors.text_secondary};
        font-size: {TYPOGRAPHY.caption_pt}pt;
    }}
    QLabel[keyHint="true"] {{
        color: {colors.text_secondary};
        background: {colors.surface_subtle};
        border: 1px solid {colors.border};
        border-radius: {RADII.radius_sm}px;
        padding: 4px 9px;
    }}
    QLabel[statusBadge="success"] {{
        color: {colors.success};
        background: {colors.success_subtle};
        border-radius: {RADII.radius_sm}px;
        padding: 4px 9px;
        font-size: {TYPOGRAPHY.caption_pt}pt;
        font-weight: 700;
    }}
    QFrame[card="true"] {{
        background: {colors.surface};
        border: 1px solid {colors.border};
        border-radius: {RADII.radius_lg}px;
    }}
    QFrame[warningBanner="true"] {{
        background: {colors.warning_subtle};
        border: 1px solid {colors.warning};
        border-radius: {RADII.radius_md}px;
    }}
    QFrame[segmented="true"] {{
        background: {colors.surface};
        border: 1px solid {colors.border};
        border-radius: {RADII.radius_md}px;
    }}
    QPushButton {{
        min-height: 34px;
        padding: 0 14px;
        border: 1px solid {colors.border_strong};
        border-radius: {RADII.radius_md}px;
        background: {colors.surface};
        color: {colors.text_primary};
    }}
    QPushButton:hover {{
        background: {colors.surface_subtle};
        border-color: {colors.border_strong};
    }}
    QPushButton:pressed {{
        background: {colors.border};
        border-color: {colors.border_strong};
    }}
    QPushButton:focus {{ border: 2px solid {colors.focus_ring}; }}
    QPushButton:disabled {{
        color: {colors.disabled_text};
        background: {colors.disabled_surface};
        border-color: {colors.border};
    }}
    QPushButton[primary="true"] {{
        color: {on_accent};
        background: {colors.primary};
        border-color: {colors.primary};
        min-height: 42px;
        border-radius: 13px;
        font-size: {TYPOGRAPHY.card_title_pt}pt;
        font-weight: 650;
    }}
    QPushButton[primary="true"]:hover {{
        background: {colors.primary_hover};
        border-color: {colors.primary_hover};
    }}
    QPushButton[primary="true"]:pressed {{
        background: {colors.primary_pressed};
        border-color: {colors.primary_pressed};
    }}
    QPushButton[compactPrimary="true"] {{
        color: {on_accent};
        background: {colors.primary};
        border-color: {colors.primary};
        min-height: 34px;
        max-height: 34px;
        border-radius: {RADII.radius_md}px;
        font-size: {TYPOGRAPHY.body_pt}pt;
        font-weight: 600;
    }}
    QPushButton[compactPrimary="true"]:hover {{
        background: {colors.primary_hover};
        border-color: {colors.primary_hover};
    }}
    QPushButton[danger="true"] {{
        color: {colors.error};
        background: {colors.error_subtle};
        border-color: {colors.error};
    }}
    QPushButton[primary="true"][danger="true"] {{
        color: white;
        background: {colors.error};
        border-color: {colors.error};
    }}
    QPushButton[primary="true"]:disabled,
    QPushButton[compactPrimary="true"]:disabled,
    QPushButton[danger="true"]:disabled {{
        color: {colors.disabled_text};
        background: {colors.disabled_surface};
        border-color: {colors.border};
    }}
    QPushButton[flat="true"] {{ border-color: transparent; background: transparent; }}
    QPushButton[flat="true"]:hover {{ background: {colors.surface_subtle}; }}
    QPushButton[segment="true"] {{
        min-height: 30px;
        padding: 0 18px;
        border-radius: {RADII.radius_sm}px;
        background: transparent;
        border-color: transparent;
        color: {colors.text_secondary};
        font-weight: 600;
    }}
    QPushButton[segment="true"]:hover {{ background: {colors.surface_subtle}; }}
    QPushButton[segment="true"]:checked {{
        color: {colors.primary};
        background: {colors.primary_subtle};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QKeySequenceEdit {{
        background: {colors.surface};
        border: 1px solid {colors.border_strong};
        border-radius: {RADII.radius_sm}px;
        padding: 7px 9px;
        selection-background-color: {colors.primary};
        selection-color: white;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus, QKeySequenceEdit:focus {{
        border: 2px solid {colors.focus_ring};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
    QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
    QKeySequenceEdit:disabled {{
        color: {colors.disabled_text};
        background: {colors.disabled_surface};
        border-color: {colors.border};
    }}
    QPlainTextEdit[contentSurface="true"], QTextEdit[contentSurface="true"] {{
        background: {colors.surface_subtle};
        border-color: {colors.border};
        padding: 11px;
    }}
    QComboBox {{ min-height: 24px; }}
    QComboBox QAbstractItemView {{
        background: {colors.surface};
        border: 1px solid {colors.border_strong};
        selection-background-color: {colors.primary_subtle};
        selection-color: {colors.text_primary};
    }}
    QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 3px 9px; }}
    QComboBox QAbstractItemView::item:hover {{ background: {colors.surface_subtle}; }}
    QComboBox QAbstractItemView::item:selected {{ background: {colors.primary_subtle}; color: {colors.text_primary}; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox:focus, QRadioButton:focus {{
        color: {colors.primary};
    }}
    QSlider::groove:horizontal {{
        height: 5px;
        border-radius: 2px;
        background: {colors.border};
    }}
    QSlider::sub-page:horizontal {{ background: {colors.primary}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 16px; margin: -6px 0; border-radius: 8px; background: {colors.primary};
    }}
    QListWidget, QTreeWidget, QTableWidget, QTableView {{
        background: {colors.surface};
        alternate-background-color: {colors.surface_subtle};
        border: 1px solid {colors.border};
        border-radius: {RADII.radius_md}px;
        outline: none;
        gridline-color: {colors.border};
    }}
    QListWidget::item, QTreeWidget::item {{
        padding: 8px;
        border-radius: 7px;
        color: {colors.text_secondary};
    }}
    QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
        background: {colors.surface_subtle};
    }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected, QTableView::item:selected {{
        background: {colors.primary_subtle};
        color: {colors.text_primary};
    }}
    QHeaderView::section {{
        background: {colors.surface_subtle};
        color: {colors.text_secondary};
        padding: 8px;
        border: none;
        border-bottom: 1px solid {colors.border};
        font-weight: 600;
    }}
    QProgressBar {{
        background: {colors.surface_subtle}; border: none; border-radius: 5px;
        min-height: 10px; max-height: 10px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {colors.primary}; border-radius: 5px; }}
    QTabWidget::pane {{
        border: 1px solid {colors.border};
        border-radius: {RADII.radius_md}px;
        background: {colors.surface};
    }}
    QTabBar::tab {{
        padding: 9px 14px;
        color: {colors.text_secondary};
        background: transparent;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{ color: {colors.text_primary}; }}
    QTabBar::tab:selected {{
        color: {colors.primary};
        border-bottom-color: {colors.primary};
        font-weight: 600;
    }}
    QSplitter::handle {{ background: {colors.border}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QMenu {{
        color: {colors.text_primary};
        background: {colors.surface};
        border: 1px solid {colors.border_strong};
        padding: 5px;
    }}
    QMenu::item {{ min-height: 16px; padding: 9px 24px 9px 10px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {colors.primary_subtle}; color: {colors.text_primary}; }}
    QMenu::separator {{ height: 1px; background: {colors.border}; margin: 5px 8px; }}
    QFrame#obsidianSidebar {{ background: {tokens["sidebar"]}; border-right: 1px solid {colors.border}; }}
    QFrame#navIndicator {{ background: {colors.primary}; border-radius: 1px; }}
    QFrame#obsidianHeader {{ background: {tokens["canvas"]}; border-bottom: 1px solid {colors.border}; }}
    QPushButton[navRow="true"] {{
        text-align: left; min-height: 38px; padding-left: 11px;
        border: 1px solid transparent; border-left: 2px solid transparent;
        background: transparent; color: {colors.text_secondary};
    }}
    QPushButton[navRow="true"]:hover {{ background: {colors.surface_subtle}; }}
    QPushButton[navRow="true"]:checked {{
        background: {colors.primary_subtle}; color: {colors.text_primary};
        border-left-color: {colors.primary};
    }}
    QPushButton[navRow="true"]:focus {{ border-color: {colors.focus_ring}; }}
    QPushButton[voiceControl="true"] {{
        min-height: 0; padding: 0; border-radius: 36px;
    }}
    QPushButton[voiceControl="true"]:focus {{ border: 2px solid {tokens["text_primary"]}; }}
    QLineEdit[animatedFocus="true"]:enabled,
    QLineEdit[animatedFocus="true"]:enabled:focus {{ border: 1px solid transparent; }}
    QToolTip {{
        color: {colors.text_primary};
        background: {colors.surface};
        border: 1px solid {colors.border_strong};
        padding: 6px;
    }}
    """


@lru_cache(maxsize=2)
def overlay_stylesheet(translucent: bool = False) -> str:
    colors = OVERLAY_PALETTE
    surface = "rgba(25, 27, 35, 245)" if translucent else colors["background"]
    return f"""
    QFrame#overlayPanel {{
        background: {surface};
        border: 1px solid {colors["border"]};
        border-radius: {RADII.radius_lg}px;
    }}
    QLabel {{
        color: {colors["text_primary"]};
        font-family: "Segoe UI Variable", "Segoe UI";
    }}
    QLabel[muted="true"] {{ color: {colors["text_secondary"]}; }}
    QLabel[tertiary="true"] {{ color: {colors["text_tertiary"]}; }}
    QLabel[overlayStatus="true"] {{ font-weight: 650; }}
    QLabel[overlayCaption="true"] {{
        color: {colors["text_tertiary"]};
        font-size: {TYPOGRAPHY.caption_pt}pt;
    }}
    QLabel[overlayLanguage="true"] {{
        color: #C7C9FF;
        background: {colors["primary_subtle"]};
        border-radius: 6px;
        font-weight: 700;
        padding: 3px 7px;
    }}
    QLabel#overlayTimerLabel {{
        font-family: "Cascadia Mono", Consolas;
        font-weight: 600;
    }}
    QPushButton[overlayIcon="true"] {{ padding: 0; min-height: 0; border-radius: 6px; }}
    QPushButton[overlayIcon="true"]:hover {{ background: {colors["surface_hover"]}; }}
    QPushButton[overlayIcon="true"]:pressed {{ background: {colors["primary_subtle"]}; }}
    QPushButton {{
        color: {colors["text_primary"]};
        background: {colors["surface"]};
        border: 1px solid transparent;
        border-radius: {RADII.radius_sm}px;
        padding: 5px 11px;
        min-height: 26px;
    }}
    QPushButton:hover {{ background: {colors["surface_hover"]}; }}
    QPushButton:pressed {{ background: {colors["primary_subtle"]}; }}
    QPushButton:disabled {{ color: {colors["text_tertiary"]}; background: {colors["surface"]}; }}
    QPushButton[primary="true"] {{
        color: white;
        background: {colors["primary"]};
        font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{ background: {colors["primary_hover"]}; }}
    QPushButton[danger="true"] {{
        color: white;
        background: {colors["error"]};
        font-weight: 600;
    }}
    QPushButton[danger="true"]:hover {{ background: {colors["error_hover"]}; }}
    QPushButton[primary="true"]:disabled, QPushButton[danger="true"]:disabled {{
        color: {colors["text_tertiary"]};
        background: {colors["surface"]};
    }}
    QPushButton[flat="true"] {{ background: transparent; color: {colors["text_secondary"]}; }}
    QPlainTextEdit#overlayResultText {{
        color: {colors["result_text"]};
        background: {colors["result_surface"]};
        border: 1px solid {colors["result_border"]};
        border-radius: {RADII.radius_sm}px;
        padding: 9px;
        selection-background-color: {colors["primary"]};
        selection-color: white;
    }}
    """


if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap

    _APP_ICON_CACHE: dict[tuple[int, str], QIcon] = {}

    class ThemeManager(QObject):
        theme_changed = Signal(object)

        def __init__(self, app: Any) -> None:
            super().__init__(app)
            self.app = app
            self.requested = ThemeMode.SYSTEM
            self.current_theme = ThemeMode.LIGHT
            self._applied = False
            self._native_palette = QPalette(app.palette())
            hints = app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._system_changed)

        def resolve_system_theme(self) -> ThemeMode:
            hints = self.app.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return ThemeMode.DARK
                if scheme == Qt.ColorScheme.Light:
                    return ThemeMode.LIGHT
            if self._applied:
                return self.current_theme
            return (
                ThemeMode.DARK
                if self._native_palette.color(QPalette.ColorRole.Window).lightness() < 128
                else ThemeMode.LIGHT
            )

        def get_token(self, name: str) -> str:
            return (DARK if self.current_theme is ThemeMode.DARK else LIGHT)[name]

        def set_theme(self, mode: ThemeMode | str) -> ThemeMode:
            self.requested = ThemeMode(mode)
            resolved = (
                self.resolve_system_theme()
                if self.requested is ThemeMode.SYSTEM
                else self.requested
            )
            if self._applied and resolved is self.current_theme:
                return resolved
            self._applied = True
            self.current_theme = resolved
            self.app.setProperty("voxtypeTheme", resolved.value)
            self.app.setFont(typography("body"))
            self.app.setPalette(_qt_palette(resolved))
            self.app.setStyleSheet(stylesheet(resolved))
            self.theme_changed.emit(resolved)
            return resolved

        def _system_changed(self, _scheme: object) -> None:
            if self.requested is ThemeMode.SYSTEM:
                self.set_theme(ThemeMode.SYSTEM)

    @lru_cache(maxsize=2)
    def _qt_palette(mode: ThemeMode) -> QPalette:
        colors = palette(mode)
        result = QPalette()
        for role, value in {
            "Window": colors.background,
            "Base": colors.surface,
            "AlternateBase": colors.surface_subtle,
            "WindowText": colors.text_primary,
            "Text": colors.text_primary,
            "ButtonText": colors.text_primary,
            "Button": colors.surface,
            "Highlight": colors.primary_subtle,
            "HighlightedText": colors.text_primary,
            "PlaceholderText": colors.text_secondary,
            "ToolTipBase": colors.surface,
            "ToolTipText": colors.text_primary,
        }.items():
            result.setColor(getattr(QPalette.ColorRole, role), QColor(value))
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            result.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.disabled_text))
        return result

    def typography(role: str) -> QFont:
        size, emphasis = {
            "hero": (28, True),
            "page": (23, True),
            "section": (16, True),
            "body": (13, False),
            "emphasis": (13, True),
            "caption": (12, False),
            "micro": (11, False),
        }[role]
        font = QFont()
        font.setFamilies(["Segoe UI Variable", "Segoe UI", "sans-serif"])
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.DemiBold if emphasis else QFont.Weight.Normal)
        return font

    def theme_manager(app: Any) -> ThemeManager:
        manager = getattr(app, "_voxtype_theme_manager", None)
        if manager is None:
            manager = ThemeManager(app)
            app._voxtype_theme_manager = manager
        return manager

    def resolved_theme(app: Any, mode: ThemeMode | str) -> ThemeMode:
        requested = ThemeMode(mode)
        return (
            theme_manager(app).resolve_system_theme()
            if requested is ThemeMode.SYSTEM
            else requested
        )

    def apply_theme(app: Any, mode: ThemeMode | str = ThemeMode.SYSTEM) -> ThemeMode:
        return theme_manager(app).set_theme(mode)

    def create_app_icon(size: int = 64, color: str = LIGHT_PALETTE.primary) -> Any:
        """Create a multi-resolution microphone icon without asset I/O."""

        requested = max(16, int(size))
        cache_key = (requested, color)
        cached = _APP_ICON_CACHE.get(cache_key)
        if cached is not None:
            return QIcon(cached)
        icon = QIcon()
        for side in sorted({16, 20, 24, 32, 48, 64, 256, requested}):
            icon.addPixmap(_render_app_icon(side, color))
        _APP_ICON_CACHE[cache_key] = icon
        return QIcon(icon)

    def _render_app_icon(side: int, color: str) -> QPixmap:
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
        return pixmap

else:

    def resolved_theme(app: Any, mode: ThemeMode | str) -> ThemeMode:
        require_pyside6()
        return ThemeMode.SYSTEM

    def apply_theme(app: Any, mode: ThemeMode | str = ThemeMode.SYSTEM) -> ThemeMode:
        require_pyside6()
        return ThemeMode.SYSTEM

    def create_app_icon(size: int = 64, color: str = LIGHT_PALETTE.primary) -> Any:
        require_pyside6()
        return None
