"""Distinguish a local Windows volume from UNC and mapped shares."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def is_local_path(path: Path) -> bool:
    if os.name != "nt":
        return path.is_absolute()
    root = path.anchor
    if not root or root.startswith("\\"):
        return False
    method = ctypes.windll.kernel32.GetDriveTypeW
    method.argtypes = (ctypes.c_wchar_p,)
    method.restype = ctypes.c_uint
    return method(root) in {2, 3, 5, 6}
