"""Online SQLite backups and confirmed, offline restore staging."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing, suppress
from pathlib import Path
from uuid import uuid4

from voxtype.constants import SESSION_SCHEMA_VERSION
from voxtype.version import __version__


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _copy_database(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + 45.0

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite backup exceeded its time budget")

    with (
        closing(
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        ) as origin,
        closing(sqlite3.connect(destination, timeout=3)) as target,
    ):
        origin.backup(target, pages=128, progress=progress, sleep=0.05)


class BackupManager:
    def __init__(self, database: Path, root: Path) -> None:
        self.database = database
        self.root = root
        self._staged = root / ".restore-pending"

    def create(self, *, automatic: bool = False) -> Path | None:
        self.root.mkdir(parents=True, exist_ok=True)
        autos = sorted(
            (
                path
                for path in self.root.glob("auto-*")
                if re.fullmatch(r"auto-[0-9]+-[0-9a-f]{8}", path.name)
                and path.is_dir()
                and not path.is_symlink()
            ),
            reverse=True,
        )
        if automatic and autos and time.time() - autos[0].stat().st_mtime < 86_400:
            return None
        prefix = "auto" if automatic else "manual"
        folder = self.root / f"{prefix}-{time.time_ns()}-{uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=self.root) as staging:
            temp = Path(staging)
            db = temp / "sessions.sqlite3"
            _copy_database(self.database, db)
            (temp / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "database_schema": SESSION_SCHEMA_VERSION,
                        "app_version": __version__,
                        "created_at_utc": time.time(),
                        "sha256": _digest(db),
                        "bytes": db.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )
            os.replace(temp, folder)
        if automatic:
            for obsolete in autos[4:]:
                self._remove_archive(obsolete)
        return folder / "manifest.json"

    def _remove_archive(self, folder: Path) -> None:
        if folder.is_symlink() or set(child.name for child in folder.iterdir()) != {
            "manifest.json",
            "sessions.sqlite3",
        }:
            return
        with suppress(OSError):
            (folder / "sessions.sqlite3").unlink()
            (folder / "manifest.json").unlink()
            folder.rmdir()

    def _validate(self, manifest: Path) -> Path:
        with manifest.open("rb") as stream:
            raw = stream.read(16_385)
        if len(raw) > 16_384 or manifest.name != "manifest.json":
            raise ValueError("Invalid backup manifest")
        meta = json.loads(raw)
        if (
            not isinstance(meta, dict)
            or meta.get("schema_version") != 1
            or type(meta.get("database_schema")) is not int
            or not 0 <= meta["database_schema"] <= SESSION_SCHEMA_VERSION
            or type(meta.get("bytes")) is not int
            or not 0 < meta["bytes"] <= 2 * 1024**3
        ):
            raise ValueError("Unsupported backup schema or size")
        database = manifest.with_name("sessions.sqlite3")
        if (
            database.is_symlink()
            or database.stat().st_size != meta["bytes"]
            or _digest(database) != meta.get("sha256")
        ):
            raise ValueError("Backup checksum mismatch")
        with closing(
            sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            schema = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                schema != meta["database_schema"]
                or not {"sessions", "session_events"} <= tables
                or connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
            ):
                raise ValueError("Backup database is invalid")
        return database

    def stage_restore(self, manifest: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self._staged.exists():
            raise FileExistsError("A restore is already pending")
        if manifest.stat().st_size > 16_384:
            raise ValueError("Backup metadata is too large")
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=self.root) as staging:
            temp = Path(staging)
            source = manifest.with_name("sessions.sqlite3")
            if source.is_symlink() or not 0 < source.stat().st_size <= 2 * 1024**3:
                raise ValueError("Backup file is not supported")
            from shutil import copyfile

            copyfile(source, temp / "sessions.sqlite3")
            copyfile(manifest, temp / "manifest.json")
            self._validate(temp / "manifest.json")
            os.replace(temp, self._staged)

    def apply_pending_restore(self) -> bool:
        if not self._staged.is_dir():
            return False
        # Rename the intent before applying so a failed restore cannot loop.
        attempt = self.root / f"restore-attempt-{uuid4().hex}"
        os.replace(self._staged, attempt)
        source = self._validate(attempt / "manifest.json")
        if self.database.is_file():
            self.create()
        _copy_database(source, self.database)
        self._remove_archive(attempt)
        return True
