"""Versioned import/export for local organizational configuration."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from voxtype.core.profiles import BUILTIN_IDS, ProfileManager
from voxtype.settings.enterprise import EnterprisePolicy
from voxtype.settings.repository import SettingsRepository
from voxtype.transcript.professional import DictionaryEntry, ProfessionalContent

MAX_PACK_BYTES = 1_048_576
_COLLECTIONS = {
    "vocabulary": "dictionary",
    "snippets": "snippets",
    "replacement_rules": "replacements",
    "command_sets": "commands",
}


@dataclass(frozen=True, slots=True)
class OrganizationProfilePack:
    schema_version: int
    organization_name: str
    profiles: list[dict[str, Any]]
    vocabulary: list[dict[str, Any]]
    snippets: list[dict[str, Any]]
    replacement_rules: list[dict[str, Any]]
    command_sets: list[dict[str, Any]]
    policy_defaults: dict[str, Any]

    @classmethod
    def read(cls, path: Path) -> OrganizationProfilePack:
        with path.open("rb") as stream:
            data = stream.read(MAX_PACK_BYTES + 1)
        if len(data) > MAX_PACK_BYTES:
            raise ValueError("Оргпакет превышает 1 МБ")
        try:
            raw = json.loads(data.decode("utf-8-sig"))
        except RecursionError as exc:
            raise ValueError("Слишком глубокая структура оргпакета") from exc
        if (
            not isinstance(raw, dict)
            or type(raw.get("schema_version")) is not int
            or raw["schema_version"] != 1
        ):
            raise ValueError("Ожидается UTF-8 JSON со схемой версии 1")
        pack = cls(**raw)
        if (
            not isinstance(pack.organization_name, str)
            or not 0 < len(pack.organization_name.strip()) <= 128
        ):
            raise ValueError("Укажите название организации")
        if not isinstance(pack.profiles, list) or len(pack.profiles) > 100:
            raise ValueError("Допускается до 100 профилей")
        policy = EnterprisePolicy.parse(pack.policy_defaults)
        executables = list(policy.disabled_applications)
        for row in pack.profiles:
            profile = ProfileManager.decode_profile(row)
            executables.extend(rule.executable for rule in profile.app_rules)
        for field, collection in _COLLECTIONS.items():
            records = ProfessionalContent.parse(collection, getattr(pack, field))
            if collection == "dictionary":
                executables.extend(item.executable for item in records)
        for executable in executables:
            if re.search(r"(?:^|[\\/])\.\.(?:[\\/]|$)", executable):
                raise ValueError("Относительный переход между каталогами запрещён")
        return pack


class OrganizationPackService:
    def __init__(
        self, repository: SettingsRepository, profiles: ProfileManager, content: ProfessionalContent
    ):
        self.repository = repository
        self.profiles = profiles
        self.content = content

    def export(self, organization: str, policy_defaults: dict[str, Any]) -> str:
        EnterprisePolicy.parse(policy_defaults)
        with self.profiles._lock, self.content._lock:
            collections = {
                key: [asdict(item) for item in self.content._collections[value]]
                for key, value in _COLLECTIONS.items()
            }
            pack = OrganizationProfilePack(
                1,
                organization,
                [asdict(item) for item in self.profiles._custom.values()],
                **collections,
                policy_defaults=policy_defaults,
            )
            payload = json.dumps(asdict(pack), ensure_ascii=False, indent=2)
        if len(payload.encode("utf-8")) > MAX_PACK_BYTES:
            raise ValueError("Оргпакет превышает 1 МБ")
        return payload

    @staticmethod
    def _key(collection: str, item: Any) -> object:
        if collection == "dictionary":
            return (
                item.canonical_text.casefold(),
                item.language,
                item.profile_id,
                item.executable,
                item.vocabulary_id,
            )
        return item.id

    def merge(self, pack: OrganizationProfilePack, *, apply: bool = False) -> tuple[str, ...]:
        preview = [f"Организация: {pack.organization_name}"]
        with self.profiles._lock, self.content._lock:
            changed = dict(self.profiles._custom)
            names = {item.name.casefold() for item in self.profiles.profiles()}
            for row in pack.profiles:
                profile = self.profiles.decode_profile(row)
                if (
                    profile.id in changed
                    or profile.id in BUILTIN_IDS
                    or profile.name.casefold() in names
                ):
                    preview.append(f"Пропуск профиля {profile.id}: конфликт ID или имени")
                    continue
                changed[profile.id] = profile
                names.add(profile.name.casefold())
            preview.append(f"Новые профили: {len(changed) - len(self.profiles._custom)}")
            if len(changed) > 100:
                raise ValueError("Превышен лимит профилей")
            new_collections: dict[str, tuple[Any, ...]] = {}
            for field, collection in _COLLECTIONS.items():
                incoming = self.content.parse(collection, getattr(pack, field))
                current = self.content._collections[collection]
                indexed = {self._key(collection, item) for item in current}
                new = []
                for item in incoming:
                    identifier = self._key(collection, item)
                    if identifier in indexed:
                        preview.append(f"Пропуск записи {collection}: ID уже существует")
                        continue
                    indexed.add(identifier)
                    new.append(
                        replace(item, origin="organization")
                        if isinstance(item, DictionaryEntry)
                        else item
                    )
                combined = (*current, *new)
                self.content.parse(collection, [asdict(item) for item in combined])
                preview.append(f"{collection}: +{len(new)}")
                new_collections[collection] = combined
            if pack.policy_defaults:
                preview.append(
                    "Предложения по политике: " + ", ".join(sorted(pack.policy_defaults))
                )
                preview.append("Административные ограничения задаются отдельно через ProgramData")
            if apply:
                self.repository.update(
                    {
                        "professional": {
                            "schema_version": 1,
                            "profiles": [asdict(item) for item in changed.values()],
                            **{
                                key: [asdict(item) for item in items]
                                for key, items in new_collections.items()
                            },
                        }
                    }
                )
                self.profiles._custom = changed
                self.content._collections = new_collections
        return tuple(preview)
