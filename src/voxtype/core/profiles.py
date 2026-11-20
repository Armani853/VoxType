"""Local dictation profiles and their foreground application assignments."""

from __future__ import annotations

import ntpath
import threading
import time
from dataclasses import asdict, dataclass, replace
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from voxtype.settings.repository import SettingsRepository

FORMATTING_MODES = ("general", "email", "business", "code", "notes", "chat", "verbatim")


@dataclass(frozen=True, slots=True)
class AppRule:
    executable: str
    title_glob: str = ""
    priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        if (not isinstance(self.executable, str) or not isinstance(self.title_glob, str)
                or type(self.priority) is not int or not -10_000 <= self.priority <= 10_000
                or type(self.enabled) is not bool):
            raise ValueError("Неверное правило приложения")
        if not self.executable.strip() or len(self.executable) > 1024:
            raise ValueError("Укажите имя или путь EXE")
        if len(self.title_glob) > 128 or sum(self.title_glob.count(i) for i in "*?[") > 8:
            raise ValueError("Шаблон заголовка слишком сложен")


@dataclass(frozen=True, slots=True)
class DictationProfile:
    id: str
    name: str
    language: str = ""
    model_id: str = ""
    formatting_mode: str = "general"
    vocabulary_id: str = ""
    command_set_id: str = ""
    snippet_set_id: str = ""
    insertion_mode: str = ""
    app_rules: tuple[AppRule, ...] = ()
    enabled: bool = True


BUILTIN_PROFILES = (
    DictationProfile("general", "General"),
    DictationProfile("business", "Business", formatting_mode="business", vocabulary_id="business"),
    DictationProfile("developer", "Developer", formatting_mode="code", vocabulary_id="developer"),
    DictationProfile("legal", "Legal", vocabulary_id="legal"),
    DictationProfile("medical", "Medical", vocabulary_id="medical"),
    DictationProfile("verbatim", "Verbatim", formatting_mode="verbatim"),
)
BUILTIN_IDS = frozenset(profile.id for profile in BUILTIN_PROFILES)


@dataclass(frozen=True, slots=True)
class DictationContext:
    executable_name: str = ""
    process_path: str | None = None
    window_title: str | None = None
    control_type: str | None = None
    selection_mode: str = "locked"
    profile_id: str = "general"
    captured_at: float = 0.0

    @classmethod
    def capture(cls, window: object | None, mode: str) -> DictationContext:
        return cls(
            executable_name=getattr(window, "process_name", ""),
            process_path=getattr(window, "process_path", None),
            window_title=getattr(window, "title", None),
            selection_mode=mode,
            captured_at=time.time(),
        )


def normalized_executable(path: str) -> str:
    return ntpath.normpath(path.strip()).casefold() if path.strip() else ""


class AppProfileResolver:
    @staticmethod
    def resolve(
        profiles: tuple[DictationProfile, ...], default_id: str, context: DictationContext
    ) -> DictationProfile:
        path = normalized_executable(context.process_path or "")
        name = normalized_executable(context.executable_name)
        title = (context.window_title or "")[:1024].casefold()
        matches: list[tuple[int, int, str, DictationProfile]] = []
        for profile in profiles:
            if not profile.enabled:
                continue
            for rule in profile.app_rules:
                executable = normalized_executable(rule.executable)
                qualified = bool(ntpath.dirname(executable))
                if not rule.enabled or executable != (path if qualified else name):
                    continue
                if rule.title_glob and not fnmatchcase(title, rule.title_glob.casefold()):
                    continue
                rank = 1 if rule.title_glob else (3 if qualified else 2)
                matches.append((-rank, -rule.priority, profile.id, profile))
        if matches:
            return min(matches, key=lambda match: match[:3])[3]
        return next(
            (item for item in profiles if item.id == default_id and item.enabled), BUILTIN_PROFILES[0]
        )


class ProfileManager:
    def __init__(self, repository: SettingsRepository, configuration: dict[str, Any]) -> None:
        self._repository = repository
        self._lock = threading.RLock()
        self._custom: dict[str, DictationProfile] = {}
        self._default_id = str(configuration.get("default_profile", "general"))
        raw_profiles = configuration.get("profiles", ())
        for raw in raw_profiles if isinstance(raw_profiles, (list, tuple)) else ():
            try:
                profile = self.decode_profile(raw)
            except (AttributeError, TypeError, ValueError):
                continue
            self._custom[profile.id] = profile

    @classmethod
    def decode_profile(cls, raw: object) -> DictationProfile:
        if not isinstance(raw, dict):
            raise ValueError("Профиль должен быть объектом")
        item = dict(raw)
        rules = item.get("app_rules", ())
        if not isinstance(rules, (list, tuple)) or len(rules) > 100:
            raise ValueError("Допускается до ста правил приложения на профиль")
        item["app_rules"] = tuple(AppRule(**rule) for rule in rules)
        profile = DictationProfile(**item)
        cls._validate(profile)
        return profile

    @property
    def default_id(self) -> str:
        with self._lock:
            return self._default_id

    def profiles(self) -> tuple[DictationProfile, ...]:
        with self._lock:
            return (*BUILTIN_PROFILES, *self._custom.values())

    def resolve(self, context: DictationContext) -> DictationProfile:
        with self._lock:
            return AppProfileResolver.resolve(self.profiles(), self._default_id, context)

    def create(self, name: str, source: DictationProfile | None = None) -> DictationProfile:
        profile = replace(
            source or BUILTIN_PROFILES[0], id=uuid4().hex, name=name.strip(), app_rules=()
        )
        self.save(profile)
        return profile

    def save(self, profile: DictationProfile) -> None:
        self._validate(profile)
        with self._lock:
            if any(
                item.id != profile.id and item.name.casefold() == profile.name.casefold()
                for item in self.profiles()
            ):
                raise ValueError("Имя профиля уже используется")
            self._commit({**self._custom, profile.id: profile}, self._default_id)

    def remove(self, profile_id: str) -> None:
        with self._lock:
            if profile_id not in self._custom:
                raise ValueError("Встроенный профиль нельзя удалить")
            custom = {key: value for key, value in self._custom.items() if key != profile_id}
            self._commit(custom, "general" if self._default_id == profile_id else self._default_id)

    def set_default(self, profile_id: str) -> None:
        with self._lock:
            if not any(item.id == profile_id and item.enabled for item in self.profiles()):
                raise ValueError("Выберите включённый профиль")
            self._commit(self._custom, profile_id)

    def _commit(self, custom: dict[str, DictationProfile], default_id: str) -> None:
        self._repository.update(
            {"professional": {
                "schema_version": 1,
                "profiles": [asdict(item) for item in custom.values()],
                "default_profile": default_id,
            }}
        )
        self._custom, self._default_id = custom, default_id

    @staticmethod
    def _validate(profile: DictationProfile) -> None:
        strings = (profile.id, profile.name, profile.language, profile.model_id, profile.formatting_mode,
                   profile.vocabulary_id, profile.command_set_id, profile.snippet_set_id, profile.insertion_mode)
        if any(not isinstance(value, str) or len(value) > 128 for value in strings):
            raise ValueError("Недопустимый тип или длина поля профиля")
        if profile.id in BUILTIN_IDS or not profile.id.isalnum() or not profile.name.strip():
            raise ValueError("Встроенные профили неизменяемы, имя обязательно")
        if type(profile.enabled) is not bool:
            raise ValueError("Неверный признак активности профиля")
        if profile.language not in ("", "ru", "en") or profile.formatting_mode not in FORMATTING_MODES:
            raise ValueError("Язык или форматирование не поддерживается")
        if profile.model_id not in ("", "small", "large-v3-turbo"):
            raise ValueError("Модель не поддерживается")
        if profile.insertion_mode not in ("", "auto", "clipboard_paste", "unicode_input", "safe_clipboard"):
            raise ValueError("Режим вставки не поддерживается")
