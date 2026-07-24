#!/usr/bin/env python3
"""Create deterministic native assets required by the Windows package."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from platformdirs import user_data_path
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from voxtype.constants import APP_AUTHOR, APP_NAME
from voxtype.runtime import install_silero_vad


def prepare(output: Path, icon_svg: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    installed_vad = install_silero_vad(user_data_path(APP_NAME, APP_AUTHOR))
    packaged_vad = output / "silero_vad.onnx"
    shutil.copy2(installed_vad, packaged_vad)

    renderer = QSvgRenderer(str(icon_svg))
    if not renderer.isValid():
        raise RuntimeError("VoxType SVG icon is invalid")
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    icon = output / "voxtype.ico"
    # PySide 6.11 advertises the ICO writer but rejects an explicit format
    # argument on Windows.  Let Qt select the writer from the .ico suffix;
    # this is also the documented QImage.save fallback when format is omitted.
    if not image.save(str(icon)):
        raise RuntimeError("Qt could not encode the Windows ICO asset")
    return packaged_vad, icon


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--icon-svg", type=Path, required=True)
    arguments = parser.parse_args()
    vad, icon = prepare(arguments.output.resolve(), arguments.icon_svg.resolve())
    print(vad)
    print(icon)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
