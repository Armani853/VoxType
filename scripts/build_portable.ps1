[CmdletBinding()]
param(
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $RepoRoot 'src'
$BuildRoot = Join-Path $RepoRoot 'build\nuitka'
$AssetRoot = Join-Path $RepoRoot 'build\packaging-assets'
$DistRoot = Join-Path $RepoRoot 'dist'
$PortableRoot = Join-Path $DistRoot 'VoxType'
$ReleaseRoot = Join-Path $RepoRoot 'release'
$Manifest = Join-Path $RepoRoot 'packaging\version_info\voxtype.manifest'
$IconSvg = Join-Path $RepoRoot 'packaging\icons\voxtype.svg'

if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
}
if (-not $SkipChecks) {
    Write-Host '[1/6] Static checks'
    & (Join-Path $PSScriptRoot 'lint.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host '[2/6] Automated tests'
    & (Join-Path $PSScriptRoot 'test.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host '[3/6] Cleaning old packaging output'
foreach ($Target in @($BuildRoot, $AssetRoot, $PortableRoot)) {
    $FullTarget = [IO.Path]::GetFullPath($Target)
    if (-not $FullTarget.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe build target: $FullTarget"
    }
    if (Test-Path -LiteralPath $FullTarget) {
        Remove-Item -LiteralPath $FullTarget -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $BuildRoot, $DistRoot, $ReleaseRoot -Force | Out-Null
$CurrentReleaseFiles = @(
    (Join-Path $ReleaseRoot 'VoxType-0.2.4-win64-portable.zip')
    (Join-Path $ReleaseRoot 'VoxType-0.2.4-win64-portable.zip.sha256')
)
foreach ($CurrentReleaseFile in $CurrentReleaseFiles) {
    if (Test-Path -LiteralPath $CurrentReleaseFile) {
        Remove-Item -LiteralPath $CurrentReleaseFile -Force
    }
}

Write-Host '[3/6] Preparing pinned VAD and native icon assets'
& $Python (Join-Path $RepoRoot 'tools\prepare_packaging_assets.py') --output $AssetRoot --icon-svg $IconSvg
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$VadAsset = Join-Path $AssetRoot 'silero_vad.onnx'
$Icon = Join-Path $AssetRoot 'voxtype.ico'
if (-not (Test-Path -LiteralPath $VadAsset)) { throw 'Pinned Silero VAD asset was not prepared' }
if (-not (Test-Path -LiteralPath $Icon)) { throw 'Windows icon asset was not prepared' }

Write-Host '[4/6] Compiling Nuitka one-folder application'
$EntryPoint = Join-Path $RepoRoot 'src\voxtype\__main__.py'
if (-not (Test-Path -LiteralPath $EntryPoint)) { throw "Missing application entry point: $EntryPoint" }
if (-not (Test-Path -LiteralPath $Manifest)) { throw "Missing application manifest: $Manifest" }
$IconArgument = $Icon.Substring($RepoRoot.Length).TrimStart('\')
$NuitkaArguments = @(
    '-m', 'nuitka',
    '--mode=standalone',
    '--assume-yes-for-downloads',
    '--jobs=6',
    '--lto=no',
    '--enable-plugin=pyside6',
    '--include-package=voxtype',
    '--include-package=faster_whisper',
    '--include-module=av.utils',
    '--include-package-data=voxtype',
    '--nofollow-import-to=IPython,jupyter,matplotlib,notebook,pandas,pygments,pytest,ruff,mypy,nuitka,scipy',
    "--include-data-files=$Manifest=VoxType.exe.manifest",
    "--include-data-files=$IconSvg=voxtype.svg",
    "--include-data-files=$VadAsset=voxtype_assets/silero_vad.onnx",
    # Nuitka treats every '#' in an absolute icon path as a resource-index
    # separator.  A relative path keeps repositories such as C:\C_C++_C# usable.
    "--windows-icon-from-ico=$IconArgument",
    '--windows-console-mode=disable',
    '--windows-company-name=VoxType',
    '--windows-product-name=VoxType',
    '--windows-file-description=Local continuous dictation',
    '--windows-file-version=0.2.4.0',
    '--windows-product-version=0.2.4.0',
    '--output-filename=VoxType.exe',
    "--output-dir=$BuildRoot",
    $EntryPoint
)
Push-Location -LiteralPath $RepoRoot
try {
    & $Python @NuitkaArguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

$CompiledDirectory = Get-ChildItem -LiteralPath $BuildRoot -Directory -Filter '*.dist' | Select-Object -First 1
if ($null -eq $CompiledDirectory) { throw 'Nuitka standalone directory was not produced' }
Move-Item -LiteralPath $CompiledDirectory.FullName -Destination $PortableRoot
$Executable = Join-Path $PortableRoot 'VoxType.exe'
if (-not (Test-Path -LiteralPath $Executable)) { throw "Missing portable executable: $Executable" }
if ((Get-Item -LiteralPath $Executable).Length -eq 0) { throw 'Portable executable is empty' }

Write-Host '[5/6] Smoke-testing packaged executable'
$Smoke = Start-Process -FilePath $Executable -ArgumentList '--self-test', '--no-ui' -Wait -PassThru -WindowStyle Hidden
if ($Smoke.ExitCode -ne 0) { throw "Packaged self-test failed with exit code $($Smoke.ExitCode)" }
$InferenceSmoke = Start-Process -FilePath $Executable -ArgumentList '--inference-smoke-test' -Wait -PassThru -WindowStyle Hidden
if ($InferenceSmoke.ExitCode -ne 0) { throw "Packaged inference smoke-test failed with exit code $($InferenceSmoke.ExitCode)" }
$PreviousSmokeData = $env:VOXTYPE_DATA_DIRECTORY
$PreviousSmokeCache = $env:VOXTYPE_CACHE_DIRECTORY
try {
    $env:VOXTYPE_DATA_DIRECTORY = Join-Path $BuildRoot 'ui-smoke-profile'
    $env:VOXTYPE_CACHE_DIRECTORY = Join-Path $BuildRoot 'ui-smoke-cache'
    $UiSmoke = Start-Process -FilePath $Executable -ArgumentList '--ui-smoke-test' -Wait -PassThru
    if ($UiSmoke.ExitCode -ne 0) { throw "Packaged Qt startup smoke-test failed with exit code $($UiSmoke.ExitCode)" }
}
finally {
    $env:VOXTYPE_DATA_DIRECTORY = $PreviousSmokeData
    $env:VOXTYPE_CACHE_DIRECTORY = $PreviousSmokeCache
}

Write-Host '[6/6] Creating ZIP and SHA-256 checksum'
$Archive = Join-Path $ReleaseRoot 'VoxType-0.2.4-win64-portable.zip'
for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
    try {
        Compress-Archive -LiteralPath $PortableRoot -DestinationPath $Archive -CompressionLevel Optimal -ErrorAction Stop
        break
    }
    catch {
        if (Test-Path -LiteralPath $Archive) {
            Remove-Item -LiteralPath $Archive -Force
        }
        if ($Attempt -eq 5) { throw }
        Start-Sleep -Seconds 2
    }
}
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Archive
$Checksum = Join-Path $ReleaseRoot 'VoxType-0.2.4-win64-portable.zip.sha256'
Set-Content -LiteralPath $Checksum -Encoding ascii -Value "$($Hash.Hash.ToLowerInvariant())  $($Hash.Path | Split-Path -Leaf)"
Write-Host "Portable: $PortableRoot"
Write-Host "Archive:  $Archive"
