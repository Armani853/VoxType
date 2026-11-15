import sys

from voxtype.windows.startup import (
    DEFAULT_STARTUP_ENABLED,
    MemoryStartupRegistryBackend,
    StartupManager,
    build_startup_command,
)


def test_startup_is_disabled_by_default_and_constructor_has_no_side_effect() -> None:
    backend = MemoryStartupRegistryBackend()
    manager = StartupManager(executable=r"C:\Program Files\VoxType\VoxType.exe", backend=backend)
    assert DEFAULT_STARTUP_ENABLED is False
    assert not manager.is_enabled()
    assert backend.values == {}


def test_enable_and_disable_touch_only_the_named_per_user_value() -> None:
    backend = MemoryStartupRegistryBackend({"OtherApp": "other.exe"})
    manager = StartupManager(
        app_name="VoxType",
        executable=r"C:\Program Files\VoxType\VoxType.exe",
        arguments=("--background",),
        backend=backend,
    )
    enabled = manager.enable()
    assert enabled.enabled
    assert backend.values["OtherApp"] == "other.exe"
    assert backend.values["VoxType"] == manager.command

    disabled = manager.disable()
    assert not disabled.enabled
    assert backend.values == {"OtherApp": "other.exe"}


def test_foreign_or_stale_command_is_not_reported_as_enabled() -> None:
    backend = MemoryStartupRegistryBackend({"VoxType": "malware.exe"})
    manager = StartupManager(executable="VoxType.exe", backend=backend)
    state = manager.state()
    assert not state.enabled
    assert state.has_foreign_value


def test_windows_command_line_quoting_handles_spaces_and_arguments() -> None:
    command = build_startup_command(
        r"C:\Program Files\VoxType\VoxType.exe",
        ("--profile", "balanced cpu"),
    )
    assert command == '"C:\\Program Files\\VoxType\\VoxType.exe" --profile "balanced cpu"'


def test_nuitka_standalone_registers_voxtype_exe_not_embedded_python(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = tmp_path / "VoxType.exe"
    app.touch()
    (tmp_path / "voxtype").mkdir()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(sys, "argv", [str(app)])

    manager = StartupManager(backend=MemoryStartupRegistryBackend())

    assert manager.command == str(app)
    assert "-m voxtype" not in manager.command
