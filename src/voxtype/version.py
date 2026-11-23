"""Application version kept separate for packaging metadata."""

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from voxtype.constants import SETTINGS_SCHEMA_VERSION

__version__ = "0.2.4"
ASR_PIPELINE_VERSION = "2"


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


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    app_version: str
    git_commit: str
    build_time_utc: str
    build_channel: str
    asr_pipeline_version: str


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


def build_identity() -> BuildIdentity:
    commit = _build_id()
    metadata = Path(sys.executable).with_name("build-identity.json")
    try:
        with metadata.open("rb") as stream:
            data = json.loads(stream.read(4097))
        if isinstance(data, dict) and (
            data.get("app_version") == __version__
            and data.get("git_commit") == commit
            and data.get("asr_pipeline_version") == ASR_PIPELINE_VERSION
            and isinstance(data.get("build_time_utc"), str)
            and data.get("build_channel") in tuple(item.value for item in ReleaseChannel)
            and datetime.fromisoformat(data["build_time_utc"]).utcoffset() is not None
        ):
            return BuildIdentity(
                __version__,
                commit,
                data["build_time_utc"],
                data["build_channel"],
                ASR_PIPELINE_VERSION,
            )
    except (OSError, ValueError):
        pass
    return BuildIdentity(__version__, commit, "", "source", ASR_PIPELINE_VERSION)
