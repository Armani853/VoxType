"""Signed update metadata, private staging and one-shot installer handoff."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from voxtype.version import ReleaseChannel, VersionInfo

_METADATA_LIMIT = 64 * 1024
_INSTALLER_LIMIT = 2 * 1024**3


class UpdatesUnavailable(RuntimeError):
    pass


def update_configuration_status() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Подписанные обновления доступны только в Windows"
    if not all(
        os.environ.get(key, "").strip()
        for key in ("VOXTYPE_UPDATE_MANIFEST", "VOXTYPE_UPDATE_PUBLIC_KEY")
    ):
        return False, "Доверенный канал обновлений не настроен"
    if find_spec("cryptography") is None:
        return False, "В этой сборке нет проверки подписи Ed25519"
    return True, ""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Update endpoints may not redirect")


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    channel: ReleaseChannel
    download_url: str
    sha256: str
    signature: str
    published_at: str
    min_os_version: tuple[int, int, int]
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StagedUpdate:
    path: Path
    info: UpdateInfo

    def launch_verified(self) -> None:
        if (
            os.name != "nt"
            or self.path.is_symlink()
            or self.path.stat().st_size != self.info.size_bytes
        ):
            raise ValueError("Invalid staged installer")
        with self.path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != self.info.sha256:
                raise ValueError("Staged installer changed")
        subprocess.Popen([str(self.path.resolve(strict=True))], cwd=self.path.parent, shell=False)


def _version(value: str) -> tuple[int, int, int, int, int]:
    match = re.fullmatch(
        r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})(?:-beta\.(0|[1-9][0-9]{0,5}))?",
        value,
    )
    if match is None:
        raise ValueError("Invalid release version")
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), int(beta is None), int(beta or 0)


def canonical_update_payload(document: dict[str, object]) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _unique_fields(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Duplicate update field")
        values[key] = value
    return values


class UpdateMetadataClient:
    def __init__(self, endpoint: str, public_key: str, version: VersionInfo) -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError as exc:
            raise UpdatesUnavailable("Ed25519 verification is unavailable") from exc
        self._endpoint = endpoint
        self._hostname = urlsplit(endpoint).hostname
        self._require_url(endpoint)
        self._verifier = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        )
        self.version = version
        self._opener = build_opener(_NoRedirects())

    @classmethod
    def from_configuration(cls, version: VersionInfo) -> UpdateMetadataClient:
        endpoint = os.environ.get("VOXTYPE_UPDATE_MANIFEST", "").strip()
        key = os.environ.get("VOXTYPE_UPDATE_PUBLIC_KEY", "").strip()
        if sys.platform != "win32" or not endpoint or not key:
            raise UpdatesUnavailable("No trusted update feed is configured")
        return cls(endpoint, key, version)

    def _require_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname != self._hostname
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not parsed.path.startswith("/")
            or any(ord(ch) <= 32 for ch in url)
            or chr(92) in url
        ):
            raise ValueError("Untrusted update URL")

    def check(self) -> UpdateInfo | None:
        deadline = time.monotonic() + 12
        body = bytearray()
        with self._opener.open(
            Request(self._endpoint, headers={"Accept": "application/json"}), timeout=8
        ) as response:
            while time.monotonic() < deadline and len(body) <= _METADATA_LIMIT:
                block = response.read1(min(8192, _METADATA_LIMIT + 1 - len(body)))
                if not block:
                    break
                body.extend(block)
        if time.monotonic() >= deadline or len(body) > _METADATA_LIMIT:
            raise ValueError("Update metadata exceeded its size or time limit")
        document = json.loads(body, object_pairs_hook=_unique_fields)
        if (
            not isinstance(document, dict)
            or set(document) != {"payload", "signature"}
            or not isinstance(document["payload"], dict)
        ):
            raise ValueError("Invalid manifest envelope")
        payload = document["payload"]
        self._verifier.verify(
            base64.b64decode(document["signature"], validate=True),
            canonical_update_payload(payload),
        )
        fields = {
            "schema_version",
            "version",
            "channel",
            "platform",
            "download_url",
            "sha256",
            "artifact_signature",
            "published_at",
            "min_os_version",
            "size_bytes",
        }
        if (
            set(payload) != fields
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
        ):
            raise ValueError("Unsupported update schema")
        target = f"windows-{platform.machine().lower()}"
        if payload["platform"] != target or payload["channel"] != self.version.release_channel:
            return None
        release = _version(payload["version"])
        if release <= _version(self.version.app_version):
            return None
        if self.version.release_channel is ReleaseChannel.STABLE and release[3] == 0:
            return None
        minimum = payload["min_os_version"]
        published = datetime.fromisoformat(payload["published_at"])
        if (
            not isinstance(minimum, list)
            or len(minimum) != 3
            or any(type(item) is not int or item < 0 for item in minimum)
            or published.tzinfo is None
            or not re.fullmatch(r"[0-9a-f]{64}", payload["sha256"])
            or type(payload["size_bytes"]) is not int
            or not 0 < payload["size_bytes"] <= _INSTALLER_LIMIT
            or len(base64.b64decode(payload["artifact_signature"], validate=True)) != 64
        ):
            raise ValueError("Invalid signed update metadata")
        if tuple(sys.getwindowsversion()[:3]) < tuple(minimum):
            return None
        self._require_url(payload["download_url"])
        return UpdateInfo(
            payload["version"],
            ReleaseChannel(payload["channel"]),
            payload["download_url"],
            payload["sha256"],
            payload["artifact_signature"],
            payload["published_at"],
            tuple(minimum),
            payload["size_bytes"],
        )

    def download(self, release: UpdateInfo, root: Path, cancel: Event) -> StagedUpdate:
        self._require_url(release.download_url)
        self._verifier.verify(
            base64.b64decode(release.signature, validate=True),
            b"VoxType installer SHA-256 v1\0" + bytes.fromhex(release.sha256),
        )
        root.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="update-", dir=root))
        temporary = folder / "download.part"
        digest = hashlib.sha256()
        received = 0
        deadline = time.monotonic() + 900
        try:
            with (
                self._opener.open(release.download_url, timeout=8) as response,
                temporary.open("xb") as stream,
            ):
                while not cancel.is_set() and time.monotonic() < deadline:
                    block = response.read1(128 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > release.size_bytes or received > _INSTALLER_LIMIT:
                        raise ValueError("Installer exceeds the signed size")
                    digest.update(block)
                    stream.write(block)
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise InterruptedError("Update download stopped")
            if received != release.size_bytes or digest.hexdigest() != release.sha256:
                raise ValueError("Installer does not match its signed release")
            installer = folder / "VoxTypeSetup.exe"
            os.replace(temporary, installer)
            return StagedUpdate(installer, release)
        finally:
            if temporary.exists():
                temporary.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()
