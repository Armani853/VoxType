from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from voxtype.sessions.repository import SessionRepository
from voxtype.ui.history_window import HistoryWindow
from voxtype.ui.models import HistoryEntry
from voxtype.ui.overlay import DictationOverlay
from voxtype.ui.settings_window import SettingsWindow


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _history_entries(total: int) -> list[HistoryEntry]:
    started = datetime(2026, 1, 1, 12, 0)
    return [
        HistoryEntry(
            session_id=f"session-{index}",
            started_at=started + timedelta(seconds=index),
            language="ru" if index % 2 == 0 else "en",  # type: ignore[arg-type]
            confirmed_text=f"deterministic transcript {index}",
            target_application=f"fixture-{index % 7}.exe",
            completion_status="completed" if index % 5 else "recovered",
        )
        for index in range(total)
    ]


def test_database_integrity_and_ten_thousand_history_query_performance(
    tmp_path: Path,
) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        rows = [
            (
                f"session-{index}",
                float(index),
                float(index + 1),
                "ru" if index % 2 == 0 else "en",
                "small",
                "balanced",
                f"fixture-{index % 7}.exe",
                f"transcript-{index}",
                "[]",
                "[]",
                1.0,
                0.2,
                0,
                "completed",
            )
            for index in range(10_000)
        ]
        with repository._connection:
            repository._connection.executemany(
                """INSERT INTO sessions(
                    session_id, started_at, finished_at, language, model, profile,
                    target_application, committed_text, warnings_json, suspicious_json,
                    audio_duration_s, processing_s, dropped_frames, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        started = time.perf_counter()
        recent = repository.recent(250)
        recent_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        found = repository._connection.execute(
            """SELECT COUNT(*) FROM sessions
            WHERE committed_text LIKE ? OR target_application LIKE ?""",
            ("%transcript-9999%", "%transcript-9999%"),
        ).fetchone()
        search_ms = (time.perf_counter() - started) * 1000
        integrity = repository._connection.execute("PRAGMA integrity_check").fetchone()
        print(
            f"DB_10K recent_ms={recent_ms:.3f} search_ms={search_ms:.3f} integrity={integrity[0]}"
        )
        assert len(recent) == 250
        assert recent[0].session_id == "session-9999"
        assert found is not None and found[0] == 1
        assert integrity is not None and integrity[0] == "ok"
        assert recent_ms < 300
        assert search_ms < 300


def test_history_and_settings_open_latency_p95(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    history = HistoryWindow()
    settings = SettingsWindow()
    qtbot.addWidget(history)
    qtbot.addWidget(settings)
    history.set_sessions(_history_entries(250))
    history_latencies: list[float] = []
    settings_latencies: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        history.show()
        QApplication.processEvents()
        history_latencies.append((time.perf_counter() - started) * 1000)
        history.hide()
        started = time.perf_counter()
        settings.show()
        QApplication.processEvents()
        settings_latencies.append((time.perf_counter() - started) * 1000)
        settings.hide()
    history_p95 = _percentile(history_latencies, 0.95)
    settings_p95 = _percentile(settings_latencies, 0.95)
    print(
        f"UI_OPEN history_p50={statistics.median(history_latencies):.3f} "
        f"history_p95={history_p95:.3f} settings_p50={statistics.median(settings_latencies):.3f} "
        f"settings_p95={settings_p95:.3f}"
    )
    assert history_p95 < 300
    assert settings_p95 < 300


def test_ui_heartbeat_and_one_hundred_window_cycles_do_not_leak_resources(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    history = HistoryWindow()
    settings = SettingsWindow()
    overlay = DictationOverlay()
    qtbot.addWidget(history)
    qtbot.addWidget(settings)
    qtbot.addWidget(overlay)
    for window in (history, settings, overlay):
        window.show()
        QApplication.processEvents()
        window.hide()
    QApplication.processEvents()
    process = psutil.Process()
    initial_rss = process.memory_info().rss
    initial_threads = process.num_threads()
    initial_handles = process.num_handles()
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(5)
    ticks: list[float] = []
    last_tick = time.perf_counter()
    cycles = 0
    midpoint_rss = initial_rss
    midpoint_threads = initial_threads
    midpoint_handles = initial_handles

    def cycle() -> None:
        nonlocal cycles, last_tick, midpoint_rss, midpoint_threads, midpoint_handles
        now = time.perf_counter()
        ticks.append((now - last_tick) * 1000)
        last_tick = now
        selected = (history, settings, overlay)[cycles % 3]
        selected.show()
        selected.hide()
        cycles += 1
        if cycles == 100:
            midpoint_rss = process.memory_info().rss
            midpoint_threads = process.num_threads()
            midpoint_handles = process.num_handles()
        if cycles >= 200:
            timer.stop()
            loop.quit()

    timer.timeout.connect(cycle)
    timer.start()
    loop.exec()
    QApplication.processEvents()
    final_rss = process.memory_info().rss
    final_threads = process.num_threads()
    final_handles = process.num_handles()
    max_stall_ms = max(ticks) - timer.interval()
    print(
        f"UI_CYCLES cycles={cycles} max_stall_ms={max_stall_ms:.3f} "
        f"rss={initial_rss}->{midpoint_rss}->{final_rss} "
        f"threads={initial_threads}->{midpoint_threads}->{final_threads} "
        f"handles={initial_handles}->{midpoint_handles}->{final_handles}"
    )
    assert cycles == 200
    assert max_stall_ms < 500
    assert final_threads <= midpoint_threads
    assert final_handles <= midpoint_handles + 1
    assert final_rss - midpoint_rss < 5 * 1024**2
