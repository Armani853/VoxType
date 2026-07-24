[CmdletBinding()]
param(
    [switch]$Diagnostics,
    [switch]$NoUi
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
}
$Arguments = @('-m', 'voxtype')
if ($Diagnostics) { $Arguments += '--diagnostics' }
if ($NoUi) { $Arguments += '--no-ui' }
& $Python @Arguments
exit $LASTEXITCODE
