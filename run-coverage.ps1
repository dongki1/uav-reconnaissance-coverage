# 정찰 범위 분석 - 실제 위성지도 위 "추가 관측 필요 영역" 보고서 생성/열기
# 사용: run-coverage.bat                       (쏘티 폴더 선택 창)
#       run-coverage.bat -Source "쏘티폴더"     (직접 지정)
#       run-coverage.bat -Open                  (최근 보고서만 열기)
# 선택: -Output 결과폴더 -Aoi 경계.geojson -Gsd 0.10 -Cell 5 -Python python.exe -FFmpegDir ffmpeg\bin -NoOpen
param(
    [string]$Source = '',
    [string]$Output = '',
    [string]$Aoi = '',
    [double]$Gsd = 0.10,
    [double]$Cell = 5,
    [string]$Python = '',
    [string]$FFmpegDir = '',
    [switch]$Open,
    [switch]$NoOpen
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$analysisRoot = Join-Path $PSScriptRoot 'analysis'

function Get-LatestReport {
    if (-not (Test-Path -LiteralPath $analysisRoot)) { return $null }
    Get-ChildItem -LiteralPath $analysisRoot -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Item -LiteralPath (Join-Path $_.FullName 'report.html') -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

function Get-LastSource {
    if (-not (Test-Path -LiteralPath $analysisRoot)) { return $null }
    $s = Get-ChildItem -LiteralPath $analysisRoot -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Item -LiteralPath (Join-Path $_.FullName 'summary.json') -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $s) { return $null }
    try { return (Get-Content -LiteralPath $s.FullName -Raw -Encoding UTF8 | ConvertFrom-Json).source } catch { return $null }
}

# ---------- 0. 기존 보고서만 열기 ----------
if ($Open) {
    $r = Get-LatestReport
    if (-not $r) { throw 'analysis 폴더에 report.html이 없습니다. 먼저 쏘티 폴더를 지정해 분석하세요.' }
    Write-Host "보고서 열기: $($r.FullName)"
    Start-Process $r.FullName
    return
}

# ---------- 1. 쏘티 폴더 ----------
if (-not $Source) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = '쏘티 폴더 선택 (REC_*.mp4, telemetry.jsonl, sortie-meta.json 이 있는 폴더)'
    $last = Get-LastSource
    if ($last -and (Test-Path -LiteralPath $last)) { $dlg.SelectedPath = $last }
    if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        $r = Get-LatestReport
        if ($r) { Write-Host "선택 취소 - 최근 보고서 열기: $($r.FullName)"; Start-Process $r.FullName; return }
        throw '쏘티 폴더를 선택하지 않았습니다.'
    }
    $Source = $dlg.SelectedPath
}
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
foreach ($need in @('sortie-meta.json', 'telemetry.jsonl')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourcePath $need))) {
        throw "쏘티 폴더에 $need 가 없습니다: $sourcePath`n(post-flight\uavN\YYYYMMDD_HHMMSS 폴더를 지정하세요)"
    }
}
if (-not $Output) {
    $leaf = Split-Path $sourcePath -Leaf
    $parent = Split-Path (Split-Path $sourcePath -Parent) -Leaf
    $Output = Join-Path $analysisRoot "${parent}_${leaf}"
}
$outputPath = [System.IO.Path]::GetFullPath($Output)
if ($outputPath.StartsWith($sourcePath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or $outputPath -eq $sourcePath) {
    throw '결과 폴더는 원본 쏘티 폴더 바깥이어야 합니다.'
}
$aoiPath = if ($Aoi) { (Resolve-Path -LiteralPath $Aoi).Path } else { '' }
Write-Host "[1/4] 쏘티: $sourcePath"
Write-Host "      결과: $outputPath"

# ---------- 2. FFmpeg ----------
function Find-Tool([string]$name) {
    if ($FFmpegDir) {
        $p = Join-Path $FFmpegDir "$name.exe"
        if (Test-Path -LiteralPath $p) { return $p }
    }
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $dirs = @("$env:LOCALAPPDATA\Microsoft\WinGet\Links", 'C:\ffmpeg\bin', "$env:ProgramFiles\ffmpeg\bin", "$env:ChocolateyInstall\bin", "$env:USERPROFILE\scoop\shims")
    foreach ($d in $dirs) { $p = Join-Path $d "$name.exe"; if ($d -and (Test-Path -LiteralPath $p)) { return $p } }
    $pkg = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\*FFmpeg*" -Filter "$name.exe" -Recurse -Depth 4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg) { return $pkg.FullName }
    return $null
}
$ffmpeg = Find-Tool 'ffmpeg'; $ffprobe = Find-Tool 'ffprobe'
if (-not $ffmpeg -or -not $ffprobe) {
    throw "ffmpeg/ffprobe를 찾지 못했습니다.`n설치: cmd에서  winget install Gyan.FFmpeg  실행 후 새 창에서 다시 실행`n또는 압축본을 받아  run-coverage.bat -FFmpegDir ""C:\ffmpeg\bin""  로 지정"
}
Write-Host "[2/4] FFmpeg: $ffmpeg"

# ---------- 3. Python (numpy, Pillow만 필요) ----------
function Get-PyVersion([string]$exe, [string[]]$pre) {
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $exe @($pre + @('-c', 'import sys;v=sys.version_info;print(v[0],v[1],sep=chr(46))')) 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return [version](@($out)[-1].ToString().Trim()) }
    } catch { }
    return $null
}
function Test-Mods([string]$exe, [string[]]$pre) {
    $ErrorActionPreference = 'Continue'
    & $exe @($pre + @('-c', 'import numpy, PIL')) 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function Test-Pip([string]$exe, [string[]]$pre) {
    $ErrorActionPreference = 'Continue'
    & $exe @($pre + @('-m', 'pip', '--version')) 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function Get-MainWorktreeRoot {
    $gitFile = Join-Path $PSScriptRoot '.git'
    if (-not (Test-Path -LiteralPath $gitFile -PathType Leaf)) { return $null }
    $line = Get-Content -LiteralPath $gitFile -Encoding UTF8 -TotalCount 1
    if ($line -match '^gitdir:\s*(.+?)[\\/]\.git[\\/]worktrees[\\/]') {
        $root = $Matches[1].Trim() -replace '/', '\'
        if (Test-Path -LiteralPath $root) { return $root }
    }
    return $null
}

$candidates = New-Object System.Collections.Generic.List[object]
if ($Python) { $candidates.Add(@{ Exe = $Python; Pre = @() }) }
$candidates.Add(@{ Exe = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'); Pre = @() })
$main = Get-MainWorktreeRoot
if ($main) { $candidates.Add(@{ Exe = (Join-Path $main '.venv\Scripts\python.exe'); Pre = @() }) }
$candidates.Add(@{ Exe = (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'); Pre = @() })
if (Get-Command py -ErrorAction SilentlyContinue) { foreach ($v in @('3.12', '3.11', '3.13', '3.10')) { $candidates.Add(@{ Exe = 'py'; Pre = @("-$v") }) } }
foreach ($n in @('python', 'python3')) {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    if ($c -and $c.Source -notlike '*WindowsApps*') { $candidates.Add(@{ Exe = $c.Source; Pre = @() }) }
}
Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python" -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
    ForEach-Object { $candidates.Add(@{ Exe = $_.FullName; Pre = @() }) }

$py = $null; $fallback = $null; $tried = @()
foreach ($c in $candidates) {
    if ($c.Exe -ne 'py' -and -not (Test-Path -LiteralPath $c.Exe)) { continue }
    $ver = Get-PyVersion $c.Exe $c.Pre
    $tried += "$($c.Exe) $($c.Pre -join ' ') -> $ver"
    if (-not $ver -or $ver -lt [version]'3.10') { continue }
    if (Test-Mods $c.Exe $c.Pre) { $py = $c; break }
    if (-not $fallback -and (Test-Pip $c.Exe $c.Pre)) { $fallback = $c }
}
if (-not $py) {
    if (-not $fallback) {
        throw "numpy/Pillow를 쓸 수 있는 Python을 찾지 못했습니다.`n시도:`n  $($tried -join "`n  ")`n해결: python.org 에서 Python 3.12 설치 후 다시 실행"
    }
    Write-Host "[3/4] numpy/Pillow 설치: $($fallback.Exe) $($fallback.Pre -join ' ')"
    & $fallback.Exe @($fallback.Pre + @('-m', 'pip', 'install', '--disable-pip-version-check', '--user', '-r', 'reconnaissance\requirements.txt'))
    if ($LASTEXITCODE -ne 0) { throw '패키지 설치 실패 - 위쪽 pip 오류를 확인하세요.' }
    $py = $fallback
}
Write-Host "[3/4] Python: $($py.Exe) $($py.Pre -join ' ')"

# ---------- 4. 분석 ----------
$argv = $py.Pre + @('-X', 'utf8', '-m', 'reconnaissance.analyze', $sourcePath, '--output', $outputPath,
    '--gsd', "$Gsd", '--cell', "$Cell", '--ffmpeg', $ffmpeg, '--ffprobe', $ffprobe)
if ($aoiPath) { $argv += @('--aoi', $aoiPath) }
Write-Host '[4/4] 분석 실행 (처음에는 영상 프레임 추출로 수 분 소요, 재실행 시 재사용)' -ForegroundColor Green
& $py.Exe @argv
if ($LASTEXITCODE -ne 0) { throw "분석 실패 (exit $LASTEXITCODE) - 위쪽 Python 오류를 확인하세요." }
$report = Join-Path $outputPath 'report.html'
Write-Host "보고서: $report" -ForegroundColor Green
if (-not $NoOpen) { Start-Process $report }
