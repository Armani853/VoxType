#!/usr/bin/env python3
"""Create deterministic native assets required by the Windows package."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from voxtype.runtime import SILERO_VAD_SHA256, install_silero_vad


def prepare(output: Path, icon_svg: Path, vad_source: Path | None = None) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    if vad_source is None:
        vad_source = install_silero_vad(output / "pinned-vad")
    if vad_source is None:
        raise RuntimeError("Pinned Silero VAD asset is unavailable")
    packaged_vad = output / "silero_vad.onnx"
    with vad_source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != SILERO_VAD_SHA256:
        raise RuntimeError("Pinned Silero VAD checksum does not match")
    shutil.copy2(vad_source, packaged_vad)

    renderer = QSvgRenderer(str(icon_svg))
    if not renderer.isValid():
        raise RuntimeError("VoxType SVG icon is invalid")
    icon = output / "voxtype.ico"
    _write_multiresolution_icon(renderer, icon)
    return packaged_vad, icon


def _write_multiresolution_icon(renderer: QSvgRenderer, destination: Path) -> None:
    """Write PNG-backed ICO frames for crisp shell, taskbar and installer sizes."""

    frames = [_render_png(renderer, side) for side in (16, 20, 24, 32, 48, 256)]
    header_size = 6 + (16 * len(frames))
    offset = header_size
    entries: list[bytes] = []
    for side, payload in zip((16, 20, 24, 32, 48, 256), frames, strict=True):
        dimension = 0 if side == 256 else side
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)
    destination.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames)) + b"".join(entries) + b"".join(frames)
    )


def _render_png(renderer: QSvgRenderer, side: int) -> bytes:
    image = QImage(side, side, QImage.Format.Format_ARGB32)
    image.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    payload = QByteArray()
    buffer = QBuffer(payload)
    qt_image = cast(Any, image)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not qt_image.save(buffer, "PNG"):
        raise RuntimeError(f"Qt could not encode the {side}px icon frame")
    buffer.close()
    return bytes(payload.data())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--icon-svg", type=Path, required=True)
    parser.add_argument("--vad-source", type=Path)
    arguments = parser.parse_args()
    vad, icon = prepare(
        arguments.output.resolve(), arguments.icon_svg.resolve(), arguments.vad_source
    )
    print(vad)
    print(icon)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
