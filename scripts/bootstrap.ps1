[CmdletBinding()]
param(
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvRoot = Join-Path $RepoRoot '.venv'
$Python = Join-Path $VenvRoot 'Scripts\python.exe'

if ($Recreate -and (Test-Path -LiteralPath $VenvRoot)) {
    $resolvedRepo = [IO.Path]::GetFullPath($RepoRoot)
    $resolvedVenv = [IO.Path]::GetFullPath($VenvRoot)
    if (-not $resolvedVenv.StartsWith($resolvedRepo, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe virtual environment path: $resolvedVenv"
    }
    Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host '[1/3] Creating Python 3.12 virtual environment'
    py -3.12-64 -m venv $VenvRoot
}

Write-Host '[2/3] Installing the verified dependency lock'
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $RepoRoot 'requirements.lock')

Write-Host '[3/3] Installing VoxType in editable mode without re-resolving dependencies'
& $Python -m pip install --no-deps -e $RepoRoot
Write-Host "Ready: $Python"
