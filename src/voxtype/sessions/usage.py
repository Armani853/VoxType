"""Local aggregate metrics without transcript or PCM storage."""

from __future__ import annotations

from typing import Any

from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository


class UsageMetricsService:
    def __init__(
        self, repository: SessionRepository, settings: SettingsRepository, typing_speed: object
    ) -> None:
        self.repository = repository
        self.settings = settings
        self._typing_speed = (
            typing_speed if type(typing_speed) is int and 10 <= typing_speed <= 150 else 40
        )

    def typing_speed(self) -> int:
        return self._typing_speed

    def save_typing_speed(self, value: int) -> None:
        if not 10 <= value <= 150:
            raise ValueError("Скорость должна быть от 10 до 150 слов в минуту")
        self.settings.update({"professional": {"typing_speed_wpm": value}})
        self._typing_speed = value

    def record(
        self,
        profile_id: str,
        status: str,
        duration: float,
        words: int,
        processing: float,
    ) -> None:
        if status == "completed":
            outcome = "успех" if words else "нет результата"
        elif status in {"canceled", "cancelled"}:
            outcome = "отменено"
        elif status == "interrupted":
            outcome = "прервано"
        else:
            outcome = "ошибка"
        self.repository.record_usage(
            profile_id,
            outcome,
            round(max(0.0, duration) * 1000),
            words,
            round(max(0.0, processing) * 1000),
        )

    def summary(self, *, weekly: bool) -> tuple[dict[str, Any], ...]:
        return self.repository.usage_summary(weekly=weekly)
