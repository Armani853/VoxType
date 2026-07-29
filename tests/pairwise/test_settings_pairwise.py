from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voxtype.core.models import Language, ModelProfile, TargetMode
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings

Setter = Callable[[AppSettings, object], None]


def _set_language(settings: AppSettings, value: object) -> None:
    settings.dictation.language = Language(str(value))


def _set_profile(settings: AppSettings, value: object) -> None:
    settings.dictation.model_profile = ModelProfile(str(value))


def _set_overlay(settings: AppSettings, value: object) -> None:
    settings.general.show_overlay = bool(value)


def _set_auto_insert(settings: AppSettings, value: object) -> None:
    settings.dictation.auto_insert = bool(value)


def _set_target(settings: AppSettings, value: object) -> None:
    settings.dictation.target_mode = TargetMode(str(value))


def _set_voice_commands(settings: AppSettings, value: object) -> None:
    settings.dictation.voice_commands = bool(value)


def _set_device(settings: AppSettings, value: object) -> None:
    settings.audio.device_id = None if value is None else int(str(value))


def _set_autostart(settings: AppSettings, value: object) -> None:
    settings.general.start_with_windows = bool(value)


FACTORS: dict[str, tuple[tuple[object, ...], Setter]] = {
    "language": (("ru", "en"), _set_language),
    "profile": (("fast", "balanced", "maximum_accuracy"), _set_profile),
    "overlay": ((False, True), _set_overlay),
    "auto_insert": ((False, True), _set_auto_insert),
    "target": (("locked", "follow_focus"), _set_target),
    "voice_commands": ((False, True), _set_voice_commands),
    "device": ((None, 0, 7), _set_device),
    "autostart": ((False, True), _set_autostart),
}


def _read(settings: AppSettings, factor: str) -> Any:
    return {
        "language": settings.dictation.language.value,
        "profile": settings.dictation.model_profile.value,
        "overlay": settings.general.show_overlay,
        "auto_insert": settings.dictation.auto_insert,
        "target": settings.dictation.target_mode.value,
        "voice_commands": settings.dictation.voice_commands,
        "device": settings.audio.device_id,
        "autostart": settings.general.start_with_windows,
    }[factor]


def test_every_pair_of_risky_settings_round_trips_without_cross_field_damage(
    tmp_path: Path,
) -> None:
    covered: set[tuple[str, object, str, object]] = set()
    factors = tuple(FACTORS)
    case = 0
    for left_index, left in enumerate(factors):
        left_values, left_setter = FACTORS[left]
        for right in factors[left_index + 1 :]:
            right_values, right_setter = FACTORS[right]
            for left_value, right_value in itertools.product(left_values, right_values):
                configured = AppSettings()
                left_setter(configured, left_value)
                right_setter(configured, right_value)
                repository = SettingsRepository(tmp_path / f"case-{case}.json")
                repository.save(configured)
                loaded = repository.load()
                assert _read(loaded, left) == left_value
                assert _read(loaded, right) == right_value
                assert loaded.dictation.injection_mode == configured.dictation.injection_mode
                covered.add((left, left_value, right, right_value))
                case += 1
    expected = sum(
        len(FACTORS[left][0]) * len(FACTORS[right][0])
        for left_index, left in enumerate(factors)
        for right in factors[left_index + 1 :]
    )
    assert len(covered) == expected


def test_known_risky_setting_triples_remain_explicit_and_local(tmp_path: Path) -> None:
    cases = (
        ("follow_focus", True, "en"),
        ("locked", False, "ru"),
        ("locked", True, "en"),
    )
    for index, (target, auto_insert, language) in enumerate(cases):
        configured = AppSettings()
        configured.dictation.target_mode = TargetMode(target)
        configured.dictation.auto_insert = auto_insert
        configured.dictation.language = Language(language)
        configured.general.show_overlay = index != 1
        repository = SettingsRepository(tmp_path / f"triple-{index}.json")
        repository.save(configured)
        loaded = repository.load()
        assert loaded.dictation.target_mode.value == target
        assert loaded.dictation.auto_insert is auto_insert
        assert loaded.dictation.language.value == language
        assert loaded.dictation.model_profile is ModelProfile.BALANCED
