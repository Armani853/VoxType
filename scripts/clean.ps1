$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$Targets = @(
    (Join-Path $RepoRoot 'build'),
    (Join-Path $RepoRoot 'dist'),
    (Join-Path $RepoRoot 'release')
)
foreach ($Target in $Targets) {
    $ResolvedTarget = [IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe clean target: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Write-Host "Removing $ResolvedTarget"
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}
