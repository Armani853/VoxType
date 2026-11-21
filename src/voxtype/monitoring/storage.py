"""Read-only accounting of application-owned storage on explicit request."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def measure_owned_paths(roots: tuple[Path, ...], *, entry_limit: int = 100_000) -> tuple[int, bool]:
    """Return logical bytes and whether all reachable owned entries were counted."""
    total = 0
    checked = 0
    complete = True

    def measure(path: Path) -> None:
        nonlocal total, checked, complete
        if checked >= entry_limit:
            complete = False
            return
        checked += 1
        try:
            info = path.lstat()
            # Windows junctions are reparse points and may not report as symlinks.
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                return
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
            elif stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as children:
                    for child in children:
                        if checked >= entry_limit:
                            complete = False
                            break
                        measure(Path(child.path))
        except FileNotFoundError:
            return
        except (OSError, RecursionError):
            complete = False

    for root in roots:
        measure(root)
    return total, complete
