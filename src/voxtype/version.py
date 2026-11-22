"""Application version kept separate for packaging metadata."""

import os
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

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


def _build_id() -> str:
    marker = Path(sys.executable).with_name("build-commit.txt")
    try:
        with marker.open("rb") as stream:
            commit = stream.read(43).strip()
        if re.fullmatch(rb"[0-9a-f]{40}", commit):
            return commit.decode("ascii")
    except OSError:
        pass
    return os.environ.get("VOXTYPE_BUILD_ID", "unknown")


def version_info(channel: ReleaseChannel = ReleaseChannel.STABLE) -> VersionInfo:
    return VersionInfo(
        __version__,
        _build_id(),
        ReleaseChannel(channel),
        SETTINGS_SCHEMA_VERSION,
        (10, 0, 0),
    )
