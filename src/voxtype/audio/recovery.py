"""Bounded microphone recovery state used by the desktop device bridge."""

from dataclasses import dataclass
from enum import StrEnum

from voxtype.audio.devices import InputDevice, match_input_device


class RecoveryState(StrEnum):
    HEALTHY = "healthy"
    INTERRUPTED = "interrupted"
    RECOVERING = "recovering"
    NEEDS_USER_ACTION = "needs_user_action"
    FAILED = "failed"


@dataclass(slots=True)
class RecoveryCoordinator:
    state: RecoveryState = RecoveryState.HEALTHY
    attempt: int = 0
    generation: int = 0

    def restart(self) -> int:
        self.generation += 1
        self.state = RecoveryState.INTERRUPTED
        self.attempt = 0
        return self.generation

    def next_delay_ms(self) -> int | None:
        delays = (500, 1500, 4000)
        if self.attempt >= len(delays):
            self.state = RecoveryState.NEEDS_USER_ACTION
            return None
        delay = delays[self.attempt]
        self.attempt += 1
        return delay


class AudioDeviceReconciler:
    @staticmethod
    def match(
        devices: tuple[InputDevice, ...], index: int | None, fingerprint: str, endpoint_id: str = ""
    ) -> InputDevice | None:
        # An index-only saved choice cannot be safely rebound after enumeration.
        if index is not None and not fingerprint and not endpoint_id:
            return None
        return match_input_device(devices, index, fingerprint, endpoint_id)
