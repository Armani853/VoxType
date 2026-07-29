from __future__ import annotations

import threading
from pathlib import Path

from voxtype.core.exceptions import InvalidStateTransition
from voxtype.core.models import Language, ModelProfile
from voxtype.core.state_machine import AppState, StateMachine
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings


def test_twenty_simultaneous_starts_produce_one_state_transition() -> None:
    machine = StateMachine(AppState.IDLE)
    barrier = threading.Barrier(20)
    successes: list[int] = []
    rejected: list[int] = []

    def start(index: int) -> None:
        barrier.wait()
        try:
            machine.transition(AppState.LISTENING, "concurrent user start")
        except InvalidStateTransition:
            rejected.append(index)
        else:
            successes.append(index)

    threads = [threading.Thread(target=start, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads)
    assert len(successes) == 1
    assert len(rejected) == 19
    assert machine.state is AppState.LISTENING
    assert len(machine.history) == 1


def test_concurrent_journal_commits_keep_unique_sequence_and_every_fragment(
    tmp_path: Path,
) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        session_id = repository.create(Language.RU, "small", ModelProfile.BALANCED, "fixture.exe")
        journal = SessionJournal(repository, session_id)
        barrier = threading.Barrier(20)
        sequences: list[int] = []

        def commit(index: int) -> None:
            barrier.wait()
            sequences.append(journal.commit_text(f"[{index}]"))

        threads = [threading.Thread(target=commit, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        record = repository.get(session_id)
        assert not any(thread.is_alive() for thread in threads)
        assert sorted(sequences) == list(range(20))
        assert record is not None
        assert all(record.committed_text.count(f"[{index}]") == 1 for index in range(20))


def test_history_reads_remain_consistent_while_sessions_are_written(tmp_path: Path) -> None:
    with SessionRepository(tmp_path / "sessions.sqlite3") as repository:
        start = threading.Barrier(2)
        finished = threading.Event()
        failures: list[BaseException] = []

        def write_sessions() -> None:
            try:
                start.wait()
                for index in range(100):
                    session_id = repository.create(
                        Language.RU,
                        "small",
                        ModelProfile.BALANCED,
                        f"fixture-{index}.exe",
                    )
                    repository.commit_text(session_id, 0, f"fragment-{index}")
                    repository.finish(session_id)
            except BaseException as exc:
                failures.append(exc)
            finally:
                finished.set()

        writer = threading.Thread(target=write_sessions)
        writer.start()
        start.wait()
        snapshots = 0
        while not finished.is_set():
            recent = repository.recent(250)
            assert len({record.session_id for record in recent}) == len(recent)
            snapshots += 1
        writer.join(timeout=3)
        assert not writer.is_alive()
        assert not failures
        assert snapshots > 0
        assert len(repository.recent(250)) == 100


def test_concurrent_atomic_settings_writes_leave_one_complete_snapshot(
    tmp_path: Path,
) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    barrier = threading.Barrier(16)
    failures: list[BaseException] = []

    def save(index: int) -> None:
        settings = AppSettings()
        settings.overlay_x = index
        settings.overlay_y = -index
        try:
            barrier.wait()
            repository.save(settings)
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=save, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    loaded = repository.load()
    assert not any(thread.is_alive() for thread in threads)
    assert not failures
    assert loaded.overlay_x in range(16)
    assert loaded.overlay_y == -loaded.overlay_x
    assert not tuple(tmp_path.glob(".settings.json.*.tmp"))
