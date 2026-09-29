param(
    [Parameter(Mandatory=$true)][string]$Source,
    [string]$Output = '',
    [string]$Aoi = '',
    [double]$Gsd = 0.10,
    [double]$Cell = 5
)
$ErrorActionPreference = 'Stop'
if (-not $Output) { $Output = Join-Path $PSScriptRoot 'analysis/coverage' }
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (Test-Path -LiteralPath $localPython) { $pythonPath = $localPython }
elseif (Test-Path -LiteralPath $bundledPython) { $pythonPath = $bundledPython }
else { $pythonPath = (Get-Command python -ErrorAction Stop).Source }
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$outputPath = [System.IO.Path]::GetFullPath($Output)
$aoiPath = if ($Aoi) { (Resolve-Path -LiteralPath $Aoi).Path } else { '' }
$arguments = @('-m', 'reconnaissance.analyze', $sourcePath, '--output', $outputPath, '--gsd', $Gsd, '--cell', $Cell)
if ($aoiPath) { $arguments += @('--aoi', $aoiPath) }
Push-Location $PSScriptRoot
try {
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "Analysis failed with exit code $LASTEXITCODE" }
    Write-Host "Report: $(Join-Path $outputPath 'report.html')"
} finally { Pop-Location }
