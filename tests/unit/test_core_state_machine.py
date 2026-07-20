from __future__ import annotations

import threading

import pytest

from voxtype.core.exceptions import InvalidStateTransition
from voxtype.core.models import Language
from voxtype.core.state_machine import AppState, StateMachine


def test_language_toggle_is_explicit() -> None:
    assert Language.RU.toggled() is Language.EN
    assert Language.EN.toggled() is Language.RU


def test_state_machine_records_valid_transition() -> None:
    machine = StateMachine()
    transition = machine.transition(AppState.IDLE, "startup complete")
    assert transition.previous is AppState.STARTING
    assert machine.state is AppState.IDLE
    assert machine.history == (transition,)


def test_state_machine_rejects_contradictory_transition() -> None:
    machine = StateMachine()
    with pytest.raises(InvalidStateTransition):
        machine.transition(AppState.TRANSCRIBING, "no active session")
    assert machine.state is AppState.STARTING


def test_state_machine_requires_reason() -> None:
    with pytest.raises(ValueError):
        StateMachine().transition(AppState.IDLE, " ")


def test_state_machine_is_serialized_between_threads() -> None:
    machine = StateMachine(AppState.IDLE)
    errors: list[Exception] = []

    def transition(target: AppState) -> None:
        try:
            machine.transition(target, f"to {target.value}")
        except InvalidStateTransition as error:
            errors.append(error)

    threads = [
        threading.Thread(target=transition, args=(AppState.LISTENING,)),
        threading.Thread(target=transition, args=(AppState.MODEL_LOADING,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(machine.history) == 1
    assert len(errors) == 1

