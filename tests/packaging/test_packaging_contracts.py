from __future__ import annotations

import re
from pathlib import Path

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
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "Compress-Archive" in script


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
    assert "signeduninstaller" not in installer.casefold()
    assert re.search(r'requestedExecutionLevel level="asInvoker"', manifest)
    assert 'uiAccess="false"' in manifest


def test_asset_preparation_uses_extension_selected_ico_writer() -> None:
    script = _read("tools/prepare_packaging_assets.py")

    assert "image.save(str(icon))" in script
    assert 'image.save(str(icon), b"ICO")' not in script
