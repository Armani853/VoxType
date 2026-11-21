"""Pure migrations for persisted settings dictionaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from voxtype.constants import SETTINGS_SCHEMA_VERSION


class UnsupportedSettingsSchema(RuntimeError):
    """The application must leave newer files untouched."""


def migrate_settings(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(raw)
    version = int(migrated.get("schema_version", 0))
    if version < 1:
        dictation = migrated.setdefault("dictation", {})
        if "safe_clipboard" in dictation and "injection_mode" not in dictation:
            dictation["injection_mode"] = (
                "safe_clipboard" if bool(dictation.pop("safe_clipboard")) else "clipboard_paste"
            )
        migrated["schema_version"] = 1
        version = 1
    if version > SETTINGS_SCHEMA_VERSION:
        raise UnsupportedSettingsSchema(
            f"Settings schema {version} is newer than supported {SETTINGS_SCHEMA_VERSION}"
        )
    return migrated
