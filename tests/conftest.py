from __future__ import annotations

from pathlib import Path

import pytest

_PATH_MARKERS = {
    "unit": "unit",
    "integration": "integration",
    "property": "property",
    "stateful": "stateful",
    "fault": "fault",
    "pairwise": "pairwise",
    "performance": "performance",
    "scenario": "scenario",
    "windows": "windows",
    "soak": "soak",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach one stable suite marker from the test directory topology."""

    for item in items:
        parts = Path(str(item.path)).parts
        for directory, marker in _PATH_MARKERS.items():
            if directory in parts:
                item.add_marker(getattr(pytest.mark, marker))
                break
