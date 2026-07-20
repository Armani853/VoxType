"""Windows integration services for VoxType.

All native libraries are loaded lazily, so importing this package is safe on
non-Windows hosts used for linting and deterministic unit tests.
"""

from .clipboard import (
    ClipboardItem,
    ClipboardManager,
    ClipboardSnapshot,
    MemoryClipboardBackend,
    Win32ClipboardBackend,
)
from .foreground import IntegrityLevel, WindowMetadata, capture_foreground
from .hotkeys import DEFAULT_HOTKEYS, GlobalHotkeyManager, Hotkey
from .injection import InjectionResult, InjectionStrategy, TextInjectionService
from .power_events import PowerEventCoordinator, PowerEventDispatcher, PowerEventKind
from .send_input import SendInputController
from .startup import DEFAULT_STARTUP_ENABLED, StartupManager
from .target_window import TargetPolicy, TargetStatus, TargetWindowManager

__all__ = [
    "DEFAULT_HOTKEYS",
    "DEFAULT_STARTUP_ENABLED",
    "ClipboardItem",
    "ClipboardManager",
    "ClipboardSnapshot",
    "GlobalHotkeyManager",
    "Hotkey",
    "InjectionResult",
    "InjectionStrategy",
    "IntegrityLevel",
    "MemoryClipboardBackend",
    "PowerEventCoordinator",
    "PowerEventDispatcher",
    "PowerEventKind",
    "SendInputController",
    "StartupManager",
    "TargetPolicy",
    "TargetStatus",
    "TargetWindowManager",
    "TextInjectionService",
    "Win32ClipboardBackend",
    "WindowMetadata",
    "capture_foreground",
]
