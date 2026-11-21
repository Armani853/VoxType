"""Environment-aware but deterministic settings defaults."""

from voxtype.settings.schema import AppSettings


def create_default_settings(logical_cores: int | None = None) -> AppSettings:
    if logical_cores is None:
        return AppSettings()

    settings = AppSettings()
    settings.model.cpu_threads = max(
        1, min(8, logical_cores - 2 if logical_cores > 4 else logical_cores)
    )
    return settings
