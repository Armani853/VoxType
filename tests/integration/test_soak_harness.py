from __future__ import annotations

from pathlib import Path

import pytest
from tools.run_soak_test import (
    SoakConfig,
    _percentile,
    _threads_are_bounded,
    run_soak,
    write_result,
)


@pytest.mark.integration
def test_accelerated_soak_preserves_fifo_drains_and_cleans_spool(tmp_path: Path) -> None:
    result = run_soak(
        SoakConfig(
            duration_seconds=0.35,
            sample_interval_seconds=0.05,
            chunk_duration_seconds=0.02,
            max_ram_chunks=2,
            spill_threshold=0.5,
            backlog_period_chunks=5,
            backlog_chunks=3,
            normal_processing_factor=0.05,
            backlog_processing_factor=1.6,
            retry_every_chunks=4,
            cascade_every_segments=1,
            drain_timeout_seconds=5.0,
        ),
        tmp_path / "work",
    )
    output = tmp_path / "result.json"
    write_result(result, output)

    assert result.passed, result.summary
    assert result.summary["captured_chunks"] == result.summary["processed_chunks"]
    assert result.summary["dropped_frames"] == 0
    assert result.summary["retry_count"]
    assert result.summary["cascade_retry_count"]
    assert result.summary["max_spool_bytes"]
    assert result.summary["resource_sample_count"] >= 2
    assert not tuple(tmp_path.rglob("*.vtxpcm"))
    assert output.is_file()


def test_soak_defaults_are_the_required_sixty_minutes_and_ten_seconds() -> None:
    config = SoakConfig()

    assert config.duration_seconds == 3600
    assert config.sample_interval_seconds == 10


def test_thread_bound_allows_transient_native_pool_but_rejects_retained_leak() -> None:
    assert _threads_are_bounded([15, 18, 15, 12], 15)
    assert not _threads_are_bounded([15, 18, 18, 18], 15)


def test_soak_percentiles_use_linear_interpolation() -> None:
    assert _percentile([1.0, 2.0, 3.0], 0.50) == 2.0
    assert _percentile([1.0, 2.0, 3.0], 0.95) == 2.9
