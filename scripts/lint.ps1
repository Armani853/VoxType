$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot 'bootstrap.ps1')
}

Write-Host '[1/3] ruff format --check'
& $Python -m ruff format --check src tests tools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '[2/3] ruff check'
& $Python -m ruff check src tests tools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '[3/3] mypy'
& $Python -m mypy
exit $LASTEXITCODE
