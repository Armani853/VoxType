"""Versioned, allowlisted personal preferences exchange."""

from __future__ import annotations

import json
import ntpath
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from voxtype.core.profiles import BUILTIN_IDS, ProfileManager
from voxtype.settings.repository import SettingsRepository
from voxtype.transcript.professional import ProfessionalContent
from voxtype.transcript.workflows import parse_workflows
from voxtype.windows.hotkeys import parse_hotkey

_COLLECTIONS = ("dictionary", "snippets", "replacements", "commands")
_HOTKEYS = ("toggle", "language", "pause")
_LIMIT = 2 * 1024**2


def _profile_for_export(row: dict) -> dict:
    row = dict(row)
    row["app_rules"] = [
        {**rule, "executable": ntpath.basename(rule["executable"])} for rule in row.get("app_rules", ())
    ]
    return row


def export_configuration(repository: SettingsRepository, path: Path) -> None:
    settings = repository.load()
    professional = settings.professional
    content = {key: professional.get(key, []) for key in (*_COLLECTIONS, "workflows")}
    content["profiles"] = [_profile_for_export(row) for row in professional.get("profiles", ())]
    content["dictionary"] = [
        {**row, "executable": ntpath.basename(row.get("executable", ""))}
        for row in content["dictionary"]
    ]
    doc = {
        "schema_version": 1, "content": content,
        "appearance": {"theme": settings.general.theme, "ui_language": settings.general.ui_language},
        "hotkeys": {key: getattr(settings.hotkeys, key) for key in _HOTKEYS},
    }
    payload = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > _LIMIT:
        raise ValueError("Export exceeds the JSON size limit")
    fd, name = tempfile.mkstemp(prefix=".voxtype-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def stage_configuration(repository: SettingsRepository, path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    with path.open("rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError("Preference package exceeds 2 MiB")
    doc = json.loads(raw)
    if not isinstance(doc, dict) or set(doc) != {"schema_version", "content", "appearance", "hotkeys"} or type(doc["schema_version"]) is not int or doc["schema_version"] != 1:
        raise ValueError("Unsupported preference package")
    general, hotkeys, content = (doc[key] for key in ("appearance", "hotkeys", "content"))
    if (
        not isinstance(general, dict) or set(general) != {"theme", "ui_language"}
        or general["theme"] not in ("dark", "light", "system") or general["ui_language"] not in ("ru", "en")
        or not isinstance(hotkeys, dict) or set(hotkeys) != set(_HOTKEYS)
        or not isinstance(content, dict) or set(content) != {*_COLLECTIONS, "profiles", "workflows"}
    ):
        raise ValueError("Invalid preference sections")
    combos = set()
    for index, (name, text) in enumerate(hotkeys.items(), 1):
        if not isinstance(text, str) or len(text) > 64:
            raise ValueError("Invalid hotkey")
        key = parse_hotkey(text, identifier=index, name=name)
        combination = key.virtual_key, key.modifiers
        if combination in combos:
            raise ValueError("Duplicate hotkeys")
        combos.add(combination)

    current = repository.load().professional
    changes = {"schema_version": 1}
    summary = []
    for name, rows in content.items():
        if not isinstance(rows, list) or len(rows) > (100 if name in {"profiles", "workflows"} else 2000):
            raise ValueError(f"Invalid collection or size: {name}")
        old = current.get(name, [])
        if not isinstance(old, list):
            raise ValueError("Current configuration is malformed")
        parse = (
            lambda entries: tuple(ProfileManager.decode_profile(row) for row in entries)
            if name == "profiles" else parse_workflows(entries) if name == "workflows"
            else ProfessionalContent.parse(name, entries)
        )
        parsed = parse(rows)
        for item in parsed:
            executables = [rule.executable for rule in getattr(item, "app_rules", ())]
            if name == "dictionary":
                executables.append(item.executable)
            if any(executable and (ntpath.basename(executable) != executable or executable in {".", ".."}
                                   or ":" in executable) for executable in executables):
                raise ValueError("Only portable executable names are accepted")
        def key_for(item):
            if name != "dictionary":
                return item.id
            return (item.canonical_text.casefold(), item.language, item.profile_id,
                    item.executable, item.vocabulary_id)
        existing = {key_for(item) for item in parse(old)}
        names = {row.get("name", "").casefold() for row in old} if name == "profiles" else set()
        if name == "profiles":
            existing.update(BUILTIN_IDS)
        add = []
        for item in parsed:
            identity = key_for(item)
            if identity in existing or (name == "profiles" and item.name.casefold() in names):
                continue
            existing.add(identity)
            if name == "profiles":
                names.add(item.name.casefold())
            add.append(asdict(item))
        changes[name] = json.loads(json.dumps([*old, *add], ensure_ascii=False))
        parse(changes[name])
        summary.append(f"{name}: добавить {len(add)}, пропустить {len(rows)-len(add)}")
    return {"professional": changes, "general": general, "hotkeys": hotkeys}, tuple(summary)
