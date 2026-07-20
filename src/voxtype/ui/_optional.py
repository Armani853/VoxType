"""Optional PySide6 support shared by all UI modules."""

from __future__ import annotations

from importlib.util import find_spec

PYSIDE6_AVAILABLE = find_spec("PySide6") is not None


def require_pyside6() -> None:
    """Raise a useful error only when someone tries to instantiate the GUI."""

    if not PYSIDE6_AVAILABLE:
        raise RuntimeError(
            "Для графического интерфейса VoxType требуется PySide6. "
            "Установите пакет с UI-зависимостями приложения."
        )
