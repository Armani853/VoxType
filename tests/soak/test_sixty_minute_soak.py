from __future__ import annotations

from pathlib import Path

import pytest
from tools.run_soak_test import SoakConfig, run_soak, write_result


@pytest.mark.soak
@pytest.mark.timeout(3900)
def test_sixty_minute_lossless_pipeline(tmp_path: Path) -> None:
    """Explicit opt-in acceptance run; excluded by the default test script."""

    result = run_soak(SoakConfig(), tmp_path / "work")
    write_result(result, tmp_path / "sixty-minute-result.json")

    assert result.passed, result.summary
    assert result.summary["wall_clock_seconds"] >= 3600
    assert result.summary["dropped_frames"] == 0
    assert result.summary["captured_chunks"] == result.summary["processed_chunks"]
    assert result.summary["resource_sample_count"] >= 361
    assert result.summary["max_spool_bytes"]
