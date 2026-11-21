"""Application version kept separate for packaging metadata."""

import os
from dataclasses import dataclass
from enum import StrEnum

from voxtype.constants import SETTINGS_SCHEMA_VERSION

__version__ = "0.2.4"


class ReleaseChannel(StrEnum):
    STABLE = "stable"
    BETA = "beta"


@dataclass(frozen=True, slots=True)
class VersionInfo:
    app_version: str
    build_id: str
    release_channel: ReleaseChannel
    minimum_supported_schema: int
    minimum_supported_windows_version: tuple[int, int, int]


def version_info(channel: ReleaseChannel = ReleaseChannel.STABLE) -> VersionInfo:
    return VersionInfo(
        __version__,
        os.environ.get("VOXTYPE_BUILD_ID", "unknown"),
        ReleaseChannel(channel),
        SETTINGS_SCHEMA_VERSION,
        (10, 0, 0),
    )
