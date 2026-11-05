from __future__ import annotations

import re
import struct
from pathlib import Path

from PySide6.QtSvg import QSvgRenderer
from tools.prepare_packaging_assets import _write_multiresolution_icon

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_portable_build_is_checked_safe_standalone_and_smoke_tested() -> None:
    script = _read("scripts/build_portable.ps1")

    assert "$ErrorActionPreference = 'Stop'" in script
    assert "'lint.ps1'" in script
    assert "'test.ps1'" in script
    assert "StartsWith($RepoRoot" in script
    assert "--mode=standalone" in script
    assert "--enable-plugin=pyside6" in script
    assert "--include-package=faster_whisper" in script
    assert "--include-module=av.utils" in script
    assert "--include-package-data=voxtype" in script
    assert "voxtype.manifest" in script
    assert "$Icon.Substring($RepoRoot.Length)" in script
    assert "Push-Location -LiteralPath $RepoRoot" in script
    assert "--self-test" in script and "--no-ui" in script
    assert "--ui-smoke-test" in script
    assert "ui-smoke-profile" in script
    assert "QT_QPA_PLATFORM = 'offscreen'" not in script
    assert re.search(
        r"Start-Process -FilePath \$Executable -ArgumentList '--ui-smoke-test' -Wait -PassThru\s*\n",
        script,
    )
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "Compress-Archive" in script and "-ErrorAction Stop" in script


def test_installer_build_cleans_smokes_and_publishes_checksum() -> None:
    script = _read("scripts/build_installer.ps1")

    assert "$ErrorActionPreference = 'Stop'" in script
    assert "StartsWith($RepoRoot" in script
    assert "build_portable.ps1" in script
    assert "Remove-Item -LiteralPath $FullTarget" in script
    assert "'/CURRENTUSER'" in script
    assert "--self-test" in script and "--no-ui" in script
    assert "unins000.exe" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "Join-Path $RepoRoot 'release'" in script


def test_inno_manifest_is_per_user_unsigned_and_not_admin() -> None:
    installer = _read("packaging/installer/VoxType.iss")
    manifest = _read("packaging/version_info/voxtype.manifest")

    assert "DefaultDirName={localappdata}" in installer
    assert "PrivilegesRequired=lowest" in installer
    assert "AppMutex=Local\\VoxType.Desktop.Singleton.v1" in installer
    assert "UninstallAppRunningError=" in installer
    assert "signeduninstaller" not in installer.casefold()
    assert re.search(r'requestedExecutionLevel level="asInvoker"', manifest)
    assert 'uiAccess="false"' in manifest


def test_asset_preparation_builds_multiresolution_windows_icon() -> None:
    script = _read("tools/prepare_packaging_assets.py")

    assert "_write_multiresolution_icon(renderer, icon)" in script
    assert "(16, 20, 24, 32, 48, 256)" in script
    assert 'struct.pack("<HHH", 0, 1, len(frames))' in script


def test_multiresolution_icon_contains_all_shell_sizes(tmp_path: Path) -> None:
    renderer = QSvgRenderer(str(ROOT / "packaging/icons/voxtype.svg"))
    destination = tmp_path / "voxtype.ico"

    _write_multiresolution_icon(renderer, destination)

    payload = destination.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", payload)
    widths = [
        256 if payload[6 + (index * 16)] == 0 else payload[6 + (index * 16)]
        for index in range(count)
    ]
    assert (reserved, image_type, count) == (0, 1, 6)
    assert widths == [16, 20, 24, 32, 48, 256]
