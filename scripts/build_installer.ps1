[CmdletBinding()]
param(
    [switch]$SkipPortableBuild,
    [switch]$SkipInstallerSmoke
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$PortableExe = Join-Path $RepoRoot 'dist\VoxType\VoxType.exe'
if (-not $SkipPortableBuild -or -not (Test-Path -LiteralPath $PortableExe)) {
    & (Join-Path $PSScriptRoot 'build_portable.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$Candidates = @(
    (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$Iscc = $Candidates | Select-Object -First 1
if (-not $Iscc) {
    throw 'Inno Setup 6 compiler not found. Install: winget install JRSoftware.InnoSetup, then rerun this script.'
}

$Output = Join-Path $RepoRoot 'dist\installer'
$ReleaseRoot = Join-Path $RepoRoot 'release'
$SmokeRoot = Join-Path $RepoRoot 'build\installer-smoke'
foreach ($Target in @($Output, $SmokeRoot)) {
    $FullTarget = [IO.Path]::GetFullPath($Target)
    if (-not $FullTarget.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe installer target: $FullTarget"
    }
    if (Test-Path -LiteralPath $FullTarget) {
        Remove-Item -LiteralPath $FullTarget -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null
$Script = Join-Path $RepoRoot 'packaging\installer\VoxType.iss'
Write-Host '[installer 1/4] Compiling per-user Inno Setup package'
& $Iscc "/DRepoRoot=$RepoRoot" "/DInstallerOutput=$Output" $Script
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$Installer = Get-ChildItem -LiteralPath $Output -Filter '*.exe' | Select-Object -First 1
if ($null -eq $Installer) { throw 'Installer executable was not produced' }
if ($Installer.Length -eq 0) { throw 'Installer executable is empty' }

Write-Host '[installer 2/4] Publishing installer release artifact'
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
$ReleaseInstaller = Join-Path $ReleaseRoot $Installer.Name
Copy-Item -LiteralPath $Installer.FullName -Destination $ReleaseInstaller -Force

if (-not $SkipInstallerSmoke) {
    Write-Host '[installer 3/4] Smoke-installing without administrator privileges'
    New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
    try {
        $Install = Start-Process -FilePath $ReleaseInstaller -ArgumentList @(
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOICONS', "/DIR=$SmokeRoot"
        ) -Wait -PassThru -WindowStyle Hidden
        if ($Install.ExitCode -ne 0) { throw "Installer smoke install failed with exit code $($Install.ExitCode)" }
        $SmokeExe = Join-Path $SmokeRoot 'VoxType.exe'
        if (-not (Test-Path -LiteralPath $SmokeExe)) { throw 'Smoke install did not produce VoxType.exe' }
        $Smoke = Start-Process -FilePath $SmokeExe -ArgumentList '--self-test', '--no-ui' -Wait -PassThru -WindowStyle Hidden
        if ($Smoke.ExitCode -ne 0) { throw "Installed self-test failed with exit code $($Smoke.ExitCode)" }
        $Uninstaller = Join-Path $SmokeRoot 'unins000.exe'
        if (-not (Test-Path -LiteralPath $Uninstaller)) { throw 'Smoke install did not produce an uninstaller' }
        $Uninstall = Start-Process -FilePath $Uninstaller -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru -WindowStyle Hidden
        if ($Uninstall.ExitCode -ne 0) { throw "Smoke uninstall failed with exit code $($Uninstall.ExitCode)" }
    }
    finally {
        if (Test-Path -LiteralPath $SmokeRoot) {
            Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
        }
    }
}
else {
    Write-Host '[installer 3/4] Installer smoke-test explicitly skipped'
}

Write-Host '[installer 4/4] Creating SHA-256 checksum'
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseInstaller
$Checksum = "$ReleaseInstaller.sha256"
Set-Content -LiteralPath $Checksum -Encoding ascii -Value "$($Hash.Hash.ToLowerInvariant())  $($Installer.Name)"
Write-Host "Installer: $ReleaseInstaller"
Write-Host "Checksum:  $Checksum"
