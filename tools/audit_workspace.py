"""Inventory workspace disk use without modifying the files being measured."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import cast


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, object]:
    files: list[tuple[str, int]] = []
    errors: list[str] = []
    directories = 0

    def onerror(error: OSError) -> None:
        errors.append(str(error))

    for current, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        dirs.sort()
        names.sort()
        directories += len(dirs)
        for name in names:
            path = Path(current) / name
            try:
                size = path.stat(follow_symlinks=False).st_size
            except OSError as error:
                errors.append(str(error))
                continue
            files.append((path.relative_to(root).as_posix(), size))

    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"bytes": 0, "files": 0})
    second_level: dict[str, dict[str, int]] = defaultdict(lambda: {"bytes": 0, "files": 0})
    by_size: dict[int, list[str]] = defaultdict(list)
    for name, size in files:
        parts = name.split("/")
        groups[parts[0]]["bytes"] += size
        groups[parts[0]]["files"] += 1
        if len(parts) > 1:
            key = "/".join(parts[:2])
            second_level[key]["bytes"] += size
            second_level[key]["files"] += 1
        if size >= 20 * 1024 * 1024:
            by_size[size].append(name)

    duplicates: dict[tuple[int, str], list[str]] = defaultdict(list)
    for size, names in by_size.items():
        if len(names) < 2:
            continue
        for name in names:
            try:
                duplicates[(size, _sha256(root / name))].append(name)
            except OSError as error:
                errors.append(str(error))

    return {
        "root": str(root),
        "bytes": sum(size for _, size in files),
        "files": len(files),
        "directories": directories,
        "top_level": groups,
        "second_level": second_level,
        "top_50_files": [
            {"path": name, "bytes": size}
            for name, size in sorted(files, key=lambda item: item[1], reverse=True)[:50]
        ],
        "sha256_duplicates_over_20_mib": [
            {"bytes_each": size, "sha256": digest, "paths": paths}
            for (size, digest), paths in duplicates.items()
            if len(paths) > 1
        ],
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    report = inventory(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"{report['files']} files, {report['directories']} directories, "
        f"{report['bytes']} bytes, {len(cast(list[str], report['errors']))} errors"
    )


if __name__ == "__main__":
    main()
