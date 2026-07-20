"""Environment-aware but deterministic settings defaults."""

import os

from voxtype.settings.schema import AppSettings


def create_default_settings(logical_cores: int | None = None) -> AppSettings:
    settings = AppSettings()
    cores = logical_cores or (os.cpu_count() or 4)
    settings.model.cpu_threads = max(1, min(8, cores - 2 if cores > 4 else cores))
    return settings
