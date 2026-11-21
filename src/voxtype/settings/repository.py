"""Atomic JSON settings persistence with corrupt-file quarantine."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast, get_type_hints

from platformdirs import user_config_path

from voxtype.constants import APP_AUTHOR, APP_NAME
from voxtype.settings.defaults import create_default_settings
from voxtype.settings.migration import migrate_settings
from voxtype.settings.schema import AppSettings


def _convert_dataclass[T](cls: type[T], values: dict[str, Any]) -> T:
    hints = get_type_hints(cls)
    converted: dict[str, Any] = {}
    for item in fields(cast(Any, cls)):
        if item.name not in values:
            continue
        value = values[item.name]
        target = hints.get(item.name)
        if isinstance(target, type) and is_dataclass(target) and isinstance(value, dict):
            converted[item.name] = _convert_dataclass(target, value)
        elif isinstance(target, type) and issubclass(target, Enum):
            converted[item.name] = target(value)
        else:
            converted[item.name] = value
    return cls(**converted)


class SettingsRepository:
    def __init__(self, path: Path | None = None):
        base = user_config_path(APP_NAME, APP_AUTHOR, roaming=True)
        self.path = path or base / "settings.json"
        self.last_recovery_backup: Path | None = None
        self._lock = threading.RLock()
        self._professional: dict[str, Any] | None = None

    def load(self) -> AppSettings:
        with self._lock:
            if not self.path.exists():
                self._professional = None
                return create_default_settings()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Settings root must be an object")
                settings = _convert_dataclass(AppSettings, migrate_settings(raw))
                if not isinstance(settings.professional, dict):
                    settings.professional = {}
                self._professional = deepcopy(settings.professional)
                return settings
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                suffix = time.strftime("%Y%m%d-%H%M%S", time.localtime())
                backup = self.path.with_name(f"settings.corrupt-{suffix}.json")
                if self.path.exists():
                    shutil.copy2(self.path, backup)
                    self.last_recovery_backup = backup
                defaults = create_default_settings()
                self.save(defaults, replace_professional=True)
                return defaults

    def save(self, settings: AppSettings, *, replace_professional: bool = False) -> None:
        with self._lock:
            if self._professional is not None and not replace_professional:
                settings.professional = deepcopy(self._professional)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                settings.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=str
            )
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(payload)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self._professional = deepcopy(settings.professional)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def update(self, changes: dict[str, Any]) -> AppSettings:
        """Deep-merge validated UI changes and persist one complete snapshot."""
        with self._lock:
            merged = self.load().to_dict()

            def merge(target: dict[str, Any], source: dict[str, Any]) -> None:
                for key, value in source.items():
                    current = target.get(key)
                    if isinstance(current, dict) and isinstance(value, dict):
                        merge(current, value)
                    else:
                        target[key] = value

            merge(merged, changes)
            settings = _convert_dataclass(AppSettings, migrate_settings(merged))
            self.save(settings, replace_professional="professional" in changes)
            return settings
