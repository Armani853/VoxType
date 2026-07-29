from dataclasses import asdict

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from voxtype.ui.theme import (
    LIGHT_PALETTE,
    RADII,
    SPACING,
    create_app_icon,
    overlay_stylesheet,
    stylesheet,
)
from voxtype.ui.widgets import ElidedLabel


def test_semantic_tokens_and_component_roles_are_complete() -> None:
    colors = asdict(LIGHT_PALETTE)
    required = {
        "background",
        "surface",
        "surface_subtle",
        "text_primary",
        "text_secondary",
        "text_tertiary",
        "border",
        "border_strong",
        "primary",
        "primary_hover",
        "primary_pressed",
        "primary_subtle",
        "success",
        "success_subtle",
        "warning",
        "warning_subtle",
        "error",
        "error_subtle",
        "focus_ring",
    }
    assert required <= colors.keys()
    assert len({colors[name] for name in required}) == len(required)
    assert tuple(asdict(SPACING).values()) == (4, 8, 12, 16, 20, 24, 32, 40)
    assert tuple(asdict(RADII).values()) == (8, 10, 16)

    light_qss = stylesheet("light")
    overlay_qss = overlay_stylesheet()
    for selector in (
        'QPushButton[compactPrimary="true"]',
        'QPushButton[danger="true"]',
        "QTableWidget::item:selected",
        'QLabel[keyHint="true"]',
        "QPushButton:focus",
    ):
        assert selector in light_qss
    assert "QPlainTextEdit#overlayResultText" in overlay_qss


def test_long_label_elides_without_losing_full_value(qtbot) -> None:  # type: ignore[no-untyped-def]
    full_text = (
        "Microphone Array (Realtek Audio) (Windows WASAPI) — an intentionally very long device name"
    )
    label = ElidedLabel(full_text)
    qtbot.addWidget(label)
    label.resize(170, 32)
    label.show()
    qtbot.waitExposed(label)

    assert label.full_text() == full_text
    assert label.text() != full_text
    assert label.toolTip() == full_text
    assert label.sizePolicy().horizontalPolicy().name == "Ignored"


def test_application_icon_exposes_native_shell_sizes() -> None:
    available = {size.width() for size in create_app_icon().availableSizes()}
    assert {16, 20, 24, 32, 48, 64, 256} <= available
    assert create_app_icon().pixmap(20, 20).deviceIndependentSize().toSize().width() == 20
