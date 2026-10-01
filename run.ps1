# 이미지 좌표추정 - 설치 + 실행 통합 스크립트 (v3)
# 사용: run.bat [-Port 8766] [-NoBrowser] [-Reinstall] [-Test] [-Python "C:\경로\python.exe"]
param(
    [int]$Port = 8766,
    [switch]$NoBrowser,
    [switch]$Reinstall,
    [switch]$Test,
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Get-PyVersion([string]$exe, [string[]]$pre) {
    # 외부 명령의 stderr 때문에 중단되지 않도록 이 함수 안에서만 Continue
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $exe @($pre + @('-c', 'import sys;v=sys.version_info;print(v[0],v[1],sep=chr(46))')) 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return [version](@($out)[-1].ToString().Trim()) }
    } catch { }
    return $null
}

function Test-PyOk($ver) { return ($ver -and $ver -ge [version]'3.10' -and $ver -lt [version]'3.14') }

function Get-MainWorktreeRoot {
    # .git 이 "gitdir: <메인저장소>/.git/worktrees/<이름>" 파일이면 메인 저장소 경로 반환
    $gitFile = Join-Path $PSScriptRoot '.git'
    if (-not (Test-Path -LiteralPath $gitFile -PathType Leaf)) { return $null }
    $line = (Get-Content -LiteralPath $gitFile -Encoding UTF8 -TotalCount 1)
    if ($line -match '^gitdir:\s*(.+?)[\\/]\.git[\\/]worktrees[\\/]') {
        $root = $Matches[1].Trim() -replace '/', '\'
        if (Test-Path -LiteralPath $root) { return $root }
    }
    return $null
}

function Find-Python {
    $tried = New-Object System.Collections.Generic.List[string]
    # 0) 직접 지정
    if ($Python) {
        $ver = Get-PyVersion $Python @()
        if (Test-PyOk $ver) { return @{ Exe = $Python; Pre = @(); Ver = $ver } }
        throw "-Python 으로 지정한 '$Python' 을 실행할 수 없거나 버전이 맞지 않습니다 (필요: 3.10~3.13, 확인: $ver)."
    }
    # 1) py 런처
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('3.12', '3.11', '3.13', '3.10')) {
            $tried.Add("py -$v")
            $ver = Get-PyVersion 'py' @("-$v")
            if (Test-PyOk $ver) { return @{ Exe = 'py'; Pre = @("-$v"); Ver = $ver } }
        }
    }
    # 2) PATH (MS Store 스텁 제외)
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $tried.Add($cmd.Source)
        if ($cmd.Source -like '*WindowsApps*') { continue }
        $ver = Get-PyVersion $cmd.Source @()
        if (Test-PyOk $ver) { return @{ Exe = $cmd.Source; Pre = @(); Ver = $ver } }
    }
    # 3) 일반 설치 위치 + Codex 내장 Python
    $candidates = @()
    foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python*", "${env:ProgramFiles(x86)}\Python*", 'C:\Python*')) {
        $candidates += Get-ChildItem -Path $base -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '\\(venv|\.venv|Lib)\\' } | ForEach-Object FullName
    }
    $candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    foreach ($exe in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $exe)) { continue }
        $tried.Add($exe)
        $ver = Get-PyVersion $exe @()
        if (Test-PyOk $ver) { return @{ Exe = $exe; Pre = @(); Ver = $ver } }
    }
    $list = if ($tried.Count) { ($tried -join "`n  ") } else { '(없음)' }
    throw "사용 가능한 Python(3.10~3.13)을 찾지 못했습니다.`n시도한 위치:`n  $list`n해결: https://www.python.org 에서 3.12 설치('Add python.exe to PATH' 체크) 또는 run.bat -Python ""C:\...\python.exe"" 로 직접 지정"
}

function Invoke-Checked([string]$exe, [string[]]$argv, [string]$what) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$what 실패 (exit $LASTEXITCODE)" }
}

# ---------- 1. 가상환경 선택/생성 ----------
$localVenv = Join-Path $PSScriptRoot '.venv'
$venvDir = $null
if ($Reinstall -and (Test-Path -LiteralPath $localVenv)) {
    Write-Host '[1/4] 기존 .venv 삭제'
    Remove-Item -LiteralPath $localVenv -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $localVenv 'Scripts\python.exe')) {
    $venvDir = $localVenv
    Write-Host "[1/4] 가상환경: $venvDir"
} elseif (-not $Reinstall -and -not $Python) {
    $main = Get-MainWorktreeRoot
    if ($main -and (Test-Path -LiteralPath (Join-Path $main '.venv\Scripts\python.exe'))) {
        $venvDir = Join-Path $main '.venv'
        Write-Host "[1/4] 메인 저장소의 가상환경 재사용: $venvDir"
    }
}
if (-not $venvDir) {
    $py = Find-Python
    Write-Host "[1/4] 가상환경 생성: Python $($py.Ver) ($($py.Exe) $($py.Pre -join ' '))"
    Invoke-Checked $py.Exe ($py.Pre + @('-m', 'venv', $localVenv)) 'venv 생성'
    $venvDir = $localVenv
}
$venvPy = Join-Path $venvDir 'Scripts\python.exe'

# ---------- 2. 패키지 ----------
function Test-Imports([string]$exe) {
    $ErrorActionPreference = 'Continue'
    & $exe -c "import numpy, PIL, ultralytics, torch" 2>$null
    return ($LASTEXITCODE -eq 0)
}
function Test-Pip([string]$exe, [string[]]$pre) {
    $ErrorActionPreference = 'Continue'
    & $exe @($pre + @('-m', 'pip', '--version')) 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

$reqFiles = @('uav_web\requirements.txt', 'reconnaissance\requirements.txt') | Where-Object { Test-Path -LiteralPath $_ }
$reqText = ($reqFiles | ForEach-Object { Get-Content -LiteralPath $_ -Raw }) -join "`n"
$sha = [System.Security.Cryptography.SHA256]::Create()
$reqHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($reqText))).Replace('-', '')
$stamp = Join-Path $venvDir '.requirements.sha256'
$installed = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }

if ($installed -eq $reqHash) {
    Write-Host '[2/4] 패키지 최신 상태'
} elseif (-not $Reinstall -and (Test-Imports $venvPy)) {
    # 이미 설치된 가상환경(예: 메인 저장소 .venv)은 다시 설치하지 않음
    Write-Host '[2/4] 필요한 패키지가 이미 설치되어 있음 (설치 생략)'
    Set-Content -LiteralPath $stamp -Value $reqHash -Encoding ASCII
} else {
    Write-Host '[2/4] 패키지 설치 (처음에는 torch 포함 수 분 소요)'
    # pip 실행기 결정: venv 내부 pip -> ensurepip -> 외부 Python의 pip --python
    $pipExe = $null; $pipPre = @()
    if (Test-Pip $venvPy @()) {
        $pipExe = $venvPy
    } else {
        Write-Host '  venv에 pip가 없어 ensurepip 시도'
        $ErrorActionPreference = 'Continue'
        & $venvPy -m ensurepip --upgrade 2>$null | Out-Null
        $ErrorActionPreference = 'Stop'
        if (Test-Pip $venvPy @()) {
            $pipExe = $venvPy
        } else {
            $base = Find-Python
            if (-not (Test-Pip $base.Exe $base.Pre)) { throw "venv와 외부 Python($($base.Exe)) 모두 pip를 사용할 수 없습니다." }
            Write-Host "  외부 pip 사용: $($base.Exe) -m pip --python"
            $pipExe = $base.Exe; $pipPre = $base.Pre + @('-m', 'pip', '--python', $venvPy)
        }
    }
    if ($pipExe -eq $venvPy) { $pipPre = @('-m', 'pip') }
    $pipArgs = $pipPre + @('install', '--disable-pip-version-check')
    foreach ($r in $reqFiles) { $pipArgs += @('-r', $r) }
    & $pipExe @pipArgs
    if ($LASTEXITCODE -ne 0) {
        throw "패키지 설치 실패 (exit $LASTEXITCODE). 위쪽의 pip 오류를 확인하세요. SSL/Proxy 오류라면 기관 네트워크 차단일 수 있습니다."
    }
    Set-Content -LiteralPath $stamp -Value $reqHash -Encoding ASCII
}

# ---------- 3. 검출 모델 ----------
Write-Host '[3/4] YOLO 모델 확인'
$modelHere = Join-Path $PSScriptRoot 'uav_web\models\yolo11n.pt'
if (-not (Test-Path -LiteralPath $modelHere)) {
    $main = Get-MainWorktreeRoot
    $modelMain = if ($main) { Join-Path $main 'uav_web\models\yolo11n.pt' } else { $null }
    if ($modelMain -and (Test-Path -LiteralPath $modelMain)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $modelHere) | Out-Null
        Copy-Item -LiteralPath $modelMain -Destination $modelHere
        Write-Host '  메인 저장소의 모델 복사'
    }
}
Invoke-Checked $venvPy @('uav_web\download_model.py') '모델 다운로드/검증'

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host '  (참고) ffmpeg/ffprobe가 PATH에 없습니다. 정찰 범위 분석(run-reconnaissance.ps1)에만 필요합니다.' -ForegroundColor Yellow
}

if ($Test) {
    Write-Host '단위 테스트 실행'
    Invoke-Checked $venvPy @('-m', 'unittest', 'discover', '-s', 'uav_web', '-p', 'test_*.py') 'uav_web 테스트'
    Invoke-Checked $venvPy @('-m', 'unittest', 'reconnaissance.test_analyze') 'reconnaissance 테스트'
}

# ---------- 4. 서버 실행 ----------
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owner = (Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    throw "포트 $Port 가 이미 사용 중입니다 (프로세스: $owner). 기존 서버를 종료하거나 -Port 8770 처럼 다른 포트를 지정하세요."
}
$url = "http://127.0.0.1:$Port"
Write-Host "[4/4] 서버 시작: $url  (종료: Ctrl+C)" -ForegroundColor Green
$server = Start-Process -FilePath $venvPy -ArgumentList @('-X', 'utf8', 'uav_web\server.py', '--port', "$Port") `
    -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
try {
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if ($server.HasExited) { throw "서버가 바로 종료되었습니다 (exit $($server.ExitCode)). 위 오류 메시지를 확인하세요." }
        try { Invoke-WebRequest -UseBasicParsing -Uri "$url/api/jobs" -TimeoutSec 2 | Out-Null; $ready = $true; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw '서버 응답이 없습니다.' }
    if (-not $NoBrowser) { Start-Process $url }
    Wait-Process -Id $server.Id
} finally {
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
