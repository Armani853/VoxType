"""Create a VoxType update manifest with the publisher's offline Ed25519 key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from voxtype.settings.updates import _INSTALLER_LIMIT, _version, canonical_update_payload
from voxtype.version import ReleaseChannel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True, help="Offline Ed25519 PEM key")
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=tuple(ReleaseChannel), required=True)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--minimum-windows", type=int, nargs=3, default=(10, 0, 0))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    url = urlsplit(args.download_url)
    version = _version(args.version)
    if not (
        url.scheme == "https"
        and url.hostname
        and url.port in (None, 443)
        and url.path.startswith("/")
        and not url.username
        and not url.password
        and not url.fragment
        and not any(ord(character) <= 32 or character == "\\" for character in args.download_url)
    ):
        parser.error("A valid HTTPS installer URL is required")
    if args.channel == ReleaseChannel.STABLE and version[3] == 0:
        parser.error("Beta versions cannot be distributed through the stable channel")
    if any(value < 0 for value in args.minimum_windows):
        parser.error("Minimum Windows version components must be non-negative")
    with args.private_key.open("rb") as stream:
        raw_key = stream.read(8193)
    if len(raw_key) > 8192:
        parser.error("Unexpected private key size")
    key = load_pem_private_key(raw_key, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("An Ed25519 private key is required")
    with args.installer.open("rb") as artifact:
        artifact.seek(0, 2)
        size = artifact.tell()
        if not 0 < size <= _INSTALLER_LIMIT:
            parser.error("Installer exceeds the permitted size")
        artifact.seek(0)
        digest = hashlib.file_digest(artifact, "sha256").digest()
        if artifact.tell() != size:
            parser.error("Installer changed while preparing the manifest")

    def sign(data: bytes) -> str:
        return base64.b64encode(key.sign(data)).decode("ascii")

    payload: dict[str, object] = {
        "schema_version": 1,
        "version": args.version,
        "channel": args.channel,
        "platform": "windows-amd64",
        "download_url": args.download_url,
        "sha256": digest.hex(),
        "artifact_signature": sign(b"VoxType installer SHA-256 v1\0" + digest),
        "published_at": datetime.now(UTC).isoformat(),
        "min_os_version": args.minimum_windows,
        "size_bytes": size,
    }
    manifest = {"payload": payload, "signature": sign(canonical_update_payload(payload))}
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(manifest, output, indent=2, sort_keys=True)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
