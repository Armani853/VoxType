[CmdletBinding()]
param(
    [switch]$IncludeInteractive,
    [switch]$IncludeSoak
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
}
$Marker = 'not interactive and not soak'
if ($IncludeInteractive -and $IncludeSoak) { $Marker = '' }
elseif ($IncludeInteractive) { $Marker = 'not soak' }
elseif ($IncludeSoak) { $Marker = 'not interactive' }

Write-Host '[test] Running pytest'
if ($Marker) { & $Python -m pytest -m $Marker }
else { & $Python -m pytest }
exit $LASTEXITCODE
