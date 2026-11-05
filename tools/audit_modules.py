"""Report module docstrings and static package imports for human review."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    modules: list[dict[str, object]] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        name = "voxtype." + ".".join(relative.with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(
                    alias.name for alias in node.names if alias.name.startswith("voxtype")
                )
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = name.split(".")[: -node.level]
                    imported = ".".join([*base, *(node.module or "").split(".")]).rstrip(".")
                else:
                    imported = node.module or ""
                if imported.startswith("voxtype"):
                    imports.add(imported)
        docstring = ast.get_docstring(tree) or ""
        modules.append(
            {
                "module": name,
                "path": relative.as_posix(),
                "lines": len(path.read_text(encoding="utf-8").splitlines()),
                "purpose_docstring": docstring.splitlines()[0] if docstring else "",
                "static_imports": sorted(imports),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(modules, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(modules)} modules inventoried")


if __name__ == "__main__":
    main()
