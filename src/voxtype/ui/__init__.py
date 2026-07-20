"""PySide6 user interface for VoxType.

Importing this package is safe in headless/core-only installations.  GUI class
construction gives a clear runtime error when the optional PySide6 dependency
is absent.
"""

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .diagnostics_window import DiagnosticsWindow
from .history_window import HistoryWindow
from .main_window import MainWindow
from .models import (
    AppStatus,
    DiagnosticReport,
    DictationLanguage,
    HistoryEntry,
    ModelProfile,
    UIState,
    diagnostic_report_from,
    format_backlog,
    format_duration,
    history_entry_from,
    snapshot_from,
    text_preview,
)
from .onboarding import OnboardingWizard
from .overlay import DictationOverlay
from .settings_window import SettingsWindow
from .theme import ThemeMode, apply_theme, create_app_icon, stylesheet
from .tray import TrayController

__all__ = [
    "PYSIDE6_AVAILABLE",
    "AppStatus",
    "DiagnosticReport",
    "DiagnosticsWindow",
    "DictationLanguage",
    "DictationOverlay",
    "HistoryEntry",
    "HistoryWindow",
    "MainWindow",
    "ModelProfile",
    "OnboardingWizard",
    "SettingsWindow",
    "ThemeMode",
    "TrayController",
    "UIState",
    "apply_theme",
    "create_app_icon",
    "diagnostic_report_from",
    "format_backlog",
    "format_duration",
    "history_entry_from",
    "require_pyside6",
    "snapshot_from",
    "stylesheet",
    "text_preview",
]
