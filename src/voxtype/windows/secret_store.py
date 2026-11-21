"""Current-user DPAPI storage for integration credentials."""

from __future__ import annotations

import ctypes
import os
import re
import tempfile
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = (("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte)))


class WindowsSecretStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
            raise ValueError("Invalid credential name")
        return self._root / f"{name}.dpapi"

    @staticmethod
    def _crypt(payload: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise OSError("Windows DPAPI is unavailable")
        if not payload or len(payload) > 65536:
            raise ValueError("Invalid credential size")
        buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        source = _Blob(len(payload), buffer)
        target = _Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = (
            ctypes.POINTER(_Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_Blob),
        )
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel32.LocalFree.restype = ctypes.c_void_p
        try:
            if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(target.data, target.size)
        finally:
            if target.data:
                ctypes.memset(target.data, 0, target.size)
                kernel32.LocalFree(target.data)
            ctypes.memset(buffer, 0, len(payload))

    def store(self, name: str, secret: bytes) -> None:
        if len(secret) > 32_768:
            raise ValueError("Credential is too large")
        path = self._path(name)
        encrypted = self._crypt(secret, decrypt=False)
        self._root.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=".secret-", dir=self._root)
        temp = Path(filename)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def load(self, name: str) -> bytes | None:
        try:
            with self._path(name).open("rb") as stream:
                payload = stream.read(65537)
        except FileNotFoundError:
            return None
        return self._crypt(payload, decrypt=True)
