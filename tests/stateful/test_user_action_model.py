from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from enum import StrEnum

from voxtype.core.models import Language
from voxtype.core.state_machine import AppState, StateMachine


class UserAction(StrEnum):
    START = "start"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    SWITCH_LANGUAGE = "switch_language"
    TARGET_LOST = "target_lost"
    TARGET_RETURNED = "target_returned"
    NEW_AUDIO = "new_audio"
    ASR_RESULT = "asr_result"
    ASR_ERROR = "asr_error"
    INSERT_SUCCESS = "insert_success"
    INSERT_FAIL = "insert_fail"
    COPY = "copy"
    CLOSE_OVERLAY = "close_overlay"
    OPEN_HISTORY = "open_history"
    DELETE_SESSION = "delete_session"
    QUIT = "quit"
    MODEL_READY = "model_ready"
    MODEL_ERROR = "model_error"


ACTIVE = {
    AppState.LISTENING,
    AppState.SPEECH_DETECTED,
    AppState.TRANSCRIBING,
    AppState.PAUSED,
    AppState.PAUSED_TARGET_LOST,
}


@dataclass(slots=True)
class VoxTypeActionModel:
    machine: StateMachine = field(default_factory=lambda: StateMachine(AppState.IDLE))
    language: Language = Language.RU
    next_sequence: int = 0
    committed: list[int] = field(default_factory=list)
    delivered: list[int] = field(default_factory=list)
    failed_delivery: set[int] = field(default_factory=set)
    accepted_audio: int = 0

    def act(self, action: UserAction) -> None:
        state = self.machine.state
        if action is UserAction.START and state in {
            AppState.IDLE,
            AppState.COMPLETED,
            AppState.ERROR_RECOVERABLE,
        }:
            target = AppState.STARTING if state is AppState.COMPLETED else AppState.LISTENING
            self.machine.transition(target, "model action start")
            return
        if action is UserAction.MODEL_READY:
            if state is AppState.STARTING:
                self.machine.transition(AppState.MODEL_LOADING, "model preparation")
            elif state is AppState.MODEL_LOADING:
                self.machine.transition(AppState.IDLE, "model ready")
            return
        if action is UserAction.MODEL_ERROR and state in {
            AppState.STARTING,
            AppState.MODEL_LOADING,
        }:
            self.machine.transition(AppState.ERROR_FATAL, "model error")
            return
        if action is UserAction.STOP and state in ACTIVE:
            self.machine.transition(AppState.FINISHING, "user stop")
            return
        if action is UserAction.PAUSE and state in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
        }:
            self.machine.transition(AppState.PAUSED, "user pause")
            return
        if action is UserAction.RESUME and state in {
            AppState.PAUSED,
            AppState.PAUSED_TARGET_LOST,
        }:
            self.machine.transition(AppState.LISTENING, "user resume")
            return
        if action is UserAction.SWITCH_LANGUAGE:
            self.language = self.language.toggled()
            return
        if action is UserAction.TARGET_LOST and state in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
        }:
            self.machine.transition(AppState.PAUSED_TARGET_LOST, "target lost")
            return
        if action is UserAction.TARGET_RETURNED and state is AppState.PAUSED_TARGET_LOST:
            self.machine.transition(AppState.LISTENING, "target returned")
            return
        if action is UserAction.NEW_AUDIO and state in {
            AppState.LISTENING,
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
        }:
            self.accepted_audio += 1
            if state is not AppState.SPEECH_DETECTED:
                self.machine.transition(AppState.SPEECH_DETECTED, "speech activity")
            return
        if action is UserAction.ASR_RESULT and state in {
            AppState.SPEECH_DETECTED,
            AppState.TRANSCRIBING,
        }:
            self.committed.append(self.next_sequence)
            self.next_sequence += 1
            if state is AppState.SPEECH_DETECTED:
                self.machine.transition(AppState.TRANSCRIBING, "ASR final")
            else:
                self.machine.transition(AppState.LISTENING, "ASR committed")
            return
        if action is UserAction.ASR_ERROR and state in ACTIVE:
            self.machine.transition(AppState.ERROR_RECOVERABLE, "ASR error")
            return
        if action is UserAction.INSERT_SUCCESS:
            pending = [sequence for sequence in self.committed if sequence not in self.delivered]
            if pending:
                self.delivered.append(pending[0])
                self.failed_delivery.discard(pending[0])
            return
        if action is UserAction.INSERT_FAIL:
            pending = [sequence for sequence in self.committed if sequence not in self.delivered]
            if pending:
                self.failed_delivery.add(pending[0])
            return
        if action is UserAction.CLOSE_OVERLAY and state is AppState.COMPLETED:
            self.machine.transition(AppState.IDLE, "result dismissed")
            return
        if (
            action is UserAction.QUIT
            and state is not AppState.SHUTTING_DOWN
            and self.machine.can_transition(AppState.SHUTTING_DOWN)
        ):
            self.machine.transition(AppState.SHUTTING_DOWN, "user quit")

    def assert_invariants(self) -> None:
        assert isinstance(self.machine.state, AppState)
        assert self.language in {Language.RU, Language.EN}
        assert self.committed == sorted(set(self.committed))
        assert self.delivered == sorted(set(self.delivered))
        assert set(self.delivered) <= set(self.committed)
        assert self.failed_delivery <= set(self.committed)
        if self.machine.state is AppState.SHUTTING_DOWN:
            before = self.accepted_audio
            self.act(UserAction.NEW_AUDIO)
            assert self.accepted_audio == before


def test_fixed_seed_model_executes_ten_thousand_actions_with_invariants() -> None:
    actions = tuple(UserAction)
    executed = 0
    for seed in range(20_260_729, 20_260_829):
        model = VoxTypeActionModel()
        randomizer = random.Random(seed)
        for _ in range(100):
            model.act(randomizer.choice(actions))
            model.assert_invariants()
            executed += 1
    assert executed == 10_000


def test_every_critical_user_action_sequence_through_length_five_is_safe() -> None:
    critical = (
        UserAction.START,
        UserAction.STOP,
        UserAction.PAUSE,
        UserAction.RESUME,
        UserAction.SWITCH_LANGUAGE,
        UserAction.QUIT,
    )
    checked = 0
    for length in range(1, 6):
        for sequence in itertools.product(critical, repeat=length):
            model = VoxTypeActionModel()
            for action in sequence:
                model.act(action)
                model.assert_invariants()
            checked += 1
    assert checked == sum(len(critical) ** length for length in range(1, 6))


def test_repeated_user_action_spam_is_a_safe_noop_or_one_valid_transition() -> None:
    model = VoxTypeActionModel()
    for action in (
        UserAction.START,
        UserAction.STOP,
        UserAction.PAUSE,
        UserAction.RESUME,
        UserAction.SWITCH_LANGUAGE,
    ):
        for _ in range(20):
            model.act(action)
            model.assert_invariants()
    assert model.committed == []
    assert model.delivered == []
    assert len(model.machine.history) <= 4
