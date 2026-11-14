[CmdletBinding()]
param(
    [switch]$SkipPortableBuild,
    [switch]$SkipInstallerSmoke,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ReleaseRoot = Join-Path $RepoRoot 'release'
$InstallerAppId = '{5A4A7C70-50ED-4F41-90A6-48C69682B1A7}_is1'
$InstallerMutex = 'Local\VoxType.Desktop.Singleton.v1'
$SmokeRoot = Join-Path $RepoRoot 'build\installer-smoke'

# Inno Setup records uninstall information by AppId, even when /DIR points at a
# temporary folder. A smoke install on the host could replace the user's
# existing uninstall entry. Reject it before any expensive build work begins.
if (-not $SkipInstallerSmoke) {
    if (Test-Path -LiteralPath $SmokeRoot) {
        throw "Installer smoke directory already exists; inspect it before retrying: $SmokeRoot"
    }
    foreach ($Hive in @('HKCU:', 'HKLM:')) {
        $UninstallKey = Join-Path $Hive "Software\Microsoft\Windows\CurrentVersion\Uninstall\$InstallerAppId"
        if (Test-Path -LiteralPath $UninstallKey) {
            throw "Installer smoke would overwrite an existing VoxType uninstall entry: $UninstallKey. Use an isolated Windows test environment."
        }
    }
    $ExistingMutex = $null
    if ([Threading.Mutex]::TryOpenExisting($InstallerMutex, [ref]$ExistingMutex)) {
        $ExistingMutex.Dispose()
        throw "Installer smoke cannot run while VoxType is active ($InstallerMutex). Use an isolated Windows test environment."
    }
}
$BuildCommit = (& git rev-parse --short HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $BuildCommit -notmatch '^[0-9a-f]+$') {
    throw 'Cannot determine build commit for installer identity'
}
$ExpectedInstaller = Join-Path $ReleaseRoot "VoxType-0.2.4-$BuildCommit-win64-setup.exe"
if ((Test-Path -LiteralPath $ExpectedInstaller) -or (Test-Path -LiteralPath "$ExpectedInstaller.sha256")) {
    throw "Release installer for commit $BuildCommit already exists: $ExpectedInstaller"
}
$PortableExe = Join-Path $RepoRoot 'dist\VoxType\VoxType.exe'
if (-not $SkipPortableBuild -or -not (Test-Path -LiteralPath $PortableExe)) {
    & (Join-Path $PSScriptRoot 'build_portable.ps1') -SkipChecks:$SkipChecks
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$PortableCommitFile = Join-Path $RepoRoot 'dist\VoxType\build-commit.txt'
$ExpectedFullCommit = (& git rev-parse HEAD).Trim()
if (-not (Test-Path -LiteralPath $PortableCommitFile)) {
    throw "Portable build provenance is missing: $PortableCommitFile"
}
$PortableCommit = (Get-Content -LiteralPath $PortableCommitFile -Raw).Trim()
if ($PortableCommit -ne $ExpectedFullCommit) {
    throw "Portable build belongs to $PortableCommit, not current commit $ExpectedFullCommit"
}
$ExpectedPortableArchive = Join-Path $ReleaseRoot "VoxType-0.2.4-$BuildCommit-win64-portable.zip"
if (-not (Test-Path -LiteralPath $ExpectedPortableArchive) -or -not (Test-Path -LiteralPath "$ExpectedPortableArchive.sha256")) {
    throw "Matching portable archive and checksum are required: $ExpectedPortableArchive"
}

$Candidates = @(
    (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$Iscc = $Candidates | Select-Object -First 1
if (-not $Iscc) {
    throw 'Inno Setup 6 compiler not found. Install: winget install JRSoftware.InnoSetup, then rerun this script.'
}

$Output = Join-Path $RepoRoot 'dist\installer'
foreach ($Target in @($Output)) {
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
$InnoStopwatch = [Diagnostics.Stopwatch]::StartNew()
& $Iscc "/DRepoRoot=$RepoRoot" "/DInstallerOutput=$Output" "/DBuildCommit=$BuildCommit" $Script
$InnoStopwatch.Stop()
Write-Host ("[build] Inno Setup compile: {0:N3} s" -f $InnoStopwatch.Elapsed.TotalSeconds)
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$Installer = Get-ChildItem -LiteralPath $Output -Filter '*.exe' | Select-Object -First 1
if ($null -eq $Installer) { throw 'Installer executable was not produced' }
if ($Installer.Length -eq 0) { throw 'Installer executable is empty' }

if (-not $SkipInstallerSmoke) {
    Write-Host '[installer 2/4] Smoke-installing without administrator privileges'
    $InstallSmokeStopwatch = [Diagnostics.Stopwatch]::StartNew()
    New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
    $SmokeUninstalled = $false
    try {
        $Install = Start-Process -FilePath $Installer.FullName -ArgumentList @(
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
        $SmokeUninstalled = $true
    }
    finally {
        if ($SmokeUninstalled -and (Test-Path -LiteralPath $SmokeRoot)) {
            Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
        }
    }
    $InstallSmokeStopwatch.Stop()
    Write-Host ("[build] Installer install/self-test/uninstall smoke: {0:N3} s" -f $InstallSmokeStopwatch.Elapsed.TotalSeconds)
}
else {
    Write-Host '[installer 2/4] Installer smoke-test explicitly skipped'
}

Write-Host '[installer 3/4] Publishing installer release artifact'
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
$ReleaseInstaller = Join-Path $ReleaseRoot $Installer.Name
Copy-Item -LiteralPath $Installer.FullName -Destination $ReleaseInstaller

Write-Host '[installer 4/4] Creating SHA-256 checksum'
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseInstaller
$Checksum = "$ReleaseInstaller.sha256"
Set-Content -LiteralPath $Checksum -Encoding ascii -Value "$($Hash.Hash.ToLowerInvariant())  $($Installer.Name)"
Write-Host "Installer: $ReleaseInstaller"
Write-Host "Checksum:  $Checksum"
