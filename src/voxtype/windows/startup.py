"""Per-user Windows startup registration, disabled until explicitly enabled."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ._win32 import require_windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_STARTUP_ENABLED = False


class StartupRegistryBackend(Protocol):
    def read(self, value_name: str) -> str | None: ...

    def write(self, value_name: str, command: str) -> None: ...

    def delete(self, value_name: str) -> bool: ...


class Win32StartupRegistryBackend:
    """Use only HKCU; VoxType never needs administrator privileges."""

    def read(self, value_name: str) -> str | None:
        require_windows()
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
            ) as key:
                value, value_type = winreg.QueryValueEx(key, value_name)
        except FileNotFoundError:
            return None
        if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            return None
        return str(value)

    def write(self, value_name: str, command: str) -> None:
        require_windows()
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)

    def delete(self, value_name: str) -> bool:
        require_windows()
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            return False
        return True


class MemoryStartupRegistryBackend:
    """In-memory registry implementation for deterministic configuration tests."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def read(self, value_name: str) -> str | None:
        return self.values.get(value_name)

    def write(self, value_name: str, command: str) -> None:
        self.values[value_name] = command

    def delete(self, value_name: str) -> bool:
        return self.values.pop(value_name, None) is not None


@dataclass(frozen=True, slots=True)
class StartupState:
    enabled: bool
    configured_command: str | None
    expected_command: str

    @property
    def has_foreign_value(self) -> bool:
        return self.configured_command is not None and not self.enabled


def build_startup_command(executable: str | Path, arguments: Sequence[str] = ()) -> str:
    executable_text = str(executable)
    if not executable_text:
        raise ValueError("startup executable cannot be empty")
    if any("\x00" in part for part in (executable_text, *arguments)):
        raise ValueError("startup command cannot contain NUL characters")
    return subprocess.list2cmdline((executable_text, *map(str, arguments)))


class StartupManager:
    """Manage one named HKCU Run value without any constructor side effects."""

    def __init__(
        self,
        *,
        app_name: str = "VoxType",
        executable: str | Path | None = None,
        arguments: Sequence[str] = (),
        backend: StartupRegistryBackend | None = None,
    ) -> None:
        if not app_name or "\x00" in app_name:
            raise ValueError("startup value name is invalid")
        self._app_name = app_name
        selected_arguments = tuple(str(argument) for argument in arguments)
        if executable is None:
            selected_executable = Path(sys.executable)
            invoked = Path(sys.argv[0]).resolve() if sys.argv else None
            packaged = (
                invoked
                if invoked is not None
                and invoked.name.casefold() == "voxtype.exe"
                and invoked.is_file()
                else selected_executable.with_name("VoxType.exe")
            )
            if packaged.is_file() and (packaged.parent / "voxtype").is_dir():
                # Nuitka standalone does not set sys.frozen and may expose its
                # bundled python.exe as sys.executable. Register the app entry
                # point, never "python.exe -m voxtype", for Windows logon.
                selected_executable = packaged
            elif not getattr(sys, "frozen", False):
                pythonw = selected_executable.with_name("pythonw.exe")
                if pythonw.exists():
                    selected_executable = pythonw
                if not selected_arguments:
                    selected_arguments = ("-m", "voxtype")
            self._executable: str | Path = selected_executable
        else:
            self._executable = executable
        self._arguments = selected_arguments
        self._backend = backend or Win32StartupRegistryBackend()
        self._lock = threading.RLock()

    @property
    def command(self) -> str:
        return build_startup_command(self._executable, self._arguments)

    def state(self) -> StartupState:
        with self._lock:
            configured = self._backend.read(self._app_name)
            expected = self.command
        return StartupState(configured == expected, configured, expected)

    def is_enabled(self) -> bool:
        return self.state().enabled

    def enable(self) -> StartupState:
        with self._lock:
            self._backend.write(self._app_name, self.command)
        return self.state()

    def disable(self) -> StartupState:
        with self._lock:
            self._backend.delete(self._app_name)
        return self.state()

    def set_enabled(self, enabled: bool) -> StartupState:
        return self.enable() if enabled else self.disable()
