[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Dev', 'QA', 'Release')]
    [string]$Mode = 'Auto',
    [switch]$Diagnostics
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Push-Location -LiteralPath $RepoRoot
try {
    if ($Mode -eq 'Auto') {
        $Changes = @(& git diff --name-only HEAD) + @(& git ls-files --others --exclude-standard)
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git changes' }
        $NeedsQA = $Changes | Where-Object {
            $_ -match '^(src/|tests/|scripts/|packaging/|tools/)' -or
            $_ -in @('pyproject.toml', 'requirements.txt', 'requirements-dev.txt')
        }
        if (-not $NeedsQA) {
            Write-Host '[workflow] Documentation-only or clean worktree; no runtime checks needed.'
            return
        }
        $Mode = 'QA'
    }

    if ($Mode -eq 'Dev') {
        Write-Host '[workflow] DEV: source launch, no Nuitka build'
        & (Join-Path $PSScriptRoot 'run.ps1') -Diagnostics:$Diagnostics
        exit $LASTEXITCODE
    }

    if ($Mode -eq 'Release') {
        $Dirty = & git status --porcelain
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git status' }
        if ($Dirty) { throw 'Release requires a clean committed worktree' }
        $Commit = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot determine release commit' }
        Write-Host "[workflow] RELEASE commit=$Commit"
    }

    foreach ($Check in @('lint.ps1', 'test.ps1')) {
        $Timer = [Diagnostics.Stopwatch]::StartNew()
        try {
            & (Join-Path $PSScriptRoot $Check)
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        finally {
            $Timer.Stop()
            Write-Host ('[workflow] {0} wall={1:N3}s' -f $Check, $Timer.Elapsed.TotalSeconds)
        }
    }

    if ($Mode -eq 'QA') {
        Write-Host '[workflow] QA passed. Use source GUI for focused interaction; package only at release freeze.'
        return
    }

    $Timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        & (Join-Path $PSScriptRoot 'build_installer.ps1') -SkipChecks
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        $Timer.Stop()
        Write-Host ('[workflow] package+installer wall={0:N3}s' -f $Timer.Elapsed.TotalSeconds)
    }
    Write-Host '[workflow] Run final installed-EXE Computer Use acceptance against this commit.'
}
finally {
    Pop-Location
}
