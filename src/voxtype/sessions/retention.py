"""Retention policy for completed sessions and temporary audio directories."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from voxtype.sessions.repository import SessionRepository


def cleanup_retention(
    repository: SessionRepository,
    temporary_audio_root: Path,
    retention_days: int,
    now: float | None = None,
    spool_root: Path | None = None,
) -> tuple[int, int]:
    if retention_days < 0:
        raise ValueError("retention_days cannot be negative")
    current = now if now is not None else time.time()
    cutoff = current - retention_days * 86_400
    deleted_sessions = repository.delete_older_than(cutoff)
    deleted_directories = 0
    if temporary_audio_root.exists():
        for child in temporary_audio_root.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
                deleted_directories += 1
            elif child.is_file() and child.stat().st_mtime < cutoff:
                child.unlink()
                deleted_directories += 1
    if spool_root is not None and spool_root.exists():
        for child in spool_root.iterdir():
            if child.stat().st_mtime >= cutoff:
                continue
            if repository.get(child.name) is not None:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            deleted_directories += 1
    return deleted_sessions, deleted_directories
