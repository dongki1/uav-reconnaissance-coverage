# 정찰 범위 분석 웹 - 브라우저에서 분석 목록/새 분석/위성지도 보고서
# 사용: run-coverage-web.bat [-Port 8780] [-NoBrowser] [-Python python.exe] [-FFmpegDir C:\ffmpeg\bin]
#       run-coverage-web.bat -Bind 100.85.62.112   (Tailscale IP에 HTTP로 직접 바인딩, 방화벽 허용 필요)
#       run-coverage-web.bat -Tailscale            (tailnet에 HTTPS로 공개: https://장비명.tailnet.ts.net/)
#       -ViewOnly  : 폴더 탐색·분석 실행을 막고 보고서 보기만 허용
[CmdletBinding()]
param(
    [int]$Port = 8780,
    [switch]$NoBrowser,
    [string]$Python = '',
    [string]$FFmpegDir = '',
    [switch]$Tailscale,
    [string]$Bind = '',
    [switch]$ViewOnly
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$webPy = Join-Path $PSScriptRoot 'reconnaissance\web.py'
if (-not (Test-Path -LiteralPath $webPy)) {
    throw 'reconnaissance\web.py 가 없습니다. 함께 받은 web.py 를 reconnaissance 폴더에 넣으세요.'
}
if (-not (Select-String -LiteralPath $webPy -Pattern '--allow-host' -SimpleMatch -Quiet)) {
    throw "reconnaissance\web.py 가 이전 버전입니다. 새로 받은 web.py 로 교체하세요.`n  위치: $webPy"
}

# ---------- 1. FFmpeg (새 분석에만 필요, 없어도 기존 보고서 보기는 가능) ----------
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
if ($ffmpeg -and $ffprobe) {
    Write-Host "[1/3] FFmpeg: $ffmpeg"
} else {
    Write-Host '[1/3] ffmpeg/ffprobe 없음 - 기존 보고서 보기만 가능 (설치: winget install Gyan.FFmpeg)' -ForegroundColor Yellow
    $ffmpeg = 'ffmpeg'; $ffprobe = 'ffprobe'
}

# ---------- 2. Python (numpy, Pillow만 필요) ----------
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
    Write-Host "[2/3] numpy/Pillow 설치: $($fallback.Exe) $($fallback.Pre -join ' ')"
    & $fallback.Exe @($fallback.Pre + @('-m', 'pip', 'install', '--disable-pip-version-check', '--user', '-r', 'reconnaissance\requirements.txt'))
    if ($LASTEXITCODE -ne 0) { throw '패키지 설치 실패 - 위쪽 pip 오류를 확인하세요.' }
    $py = $fallback
}
Write-Host "[2/3] Python: $($py.Exe) $($py.Pre -join ' ')"

# ---------- 3. 웹 서버 ----------
$bindHost = if ($Bind) { $Bind } else { '127.0.0.1' }
$allowHosts = @()
if ($Bind) { $allowHosts += $Bind }
$ts = $null; $tsName = $null
if ($Tailscale) {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    $ts = if ($cmd) { $cmd.Source } elseif (Test-Path -LiteralPath "$env:ProgramFiles\Tailscale\tailscale.exe") { "$env:ProgramFiles\Tailscale\tailscale.exe" } else { $null }
    if (-not $ts) { throw 'tailscale.exe 를 찾지 못했습니다. Tailscale 설치/로그인 상태를 확인하세요.' }
    $status = (& $ts status --json) -join "`n" | ConvertFrom-Json
    if (-not $status.Self) { throw 'tailscale status 를 읽지 못했습니다. Tailscale 로그인 상태를 확인하세요.' }
    $tsName = ([string]$status.Self.DNSName).TrimEnd('.')
    if (-not $tsName) { throw 'MagicDNS 이름이 없습니다. Tailscale 관리 콘솔에서 MagicDNS 를 켜세요.' }
    $allowHosts += $tsName
    $allowHosts += @($status.Self.TailscaleIPs | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' })
}
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owner = (Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    throw "포트 $Port 가 이미 사용 중입니다 (프로세스: $owner). 기존 서버 창을 닫거나 -Port 8781 처럼 바꾸세요."
}
$localUrl = "http://127.0.0.1:$Port"
$probeUrl = if ($Bind) { "http://${Bind}:$Port" } else { $localUrl }
$argv = $py.Pre + @('-X', 'utf8', '-m', 'reconnaissance.web', '--port', "$Port", '--host', $bindHost,
    '--ffmpeg', "`"$ffmpeg`"", '--ffprobe', "`"$ffprobe`"")
foreach ($h in ($allowHosts | Select-Object -Unique)) { $argv += @('--allow-host', $h) }
if ($ViewOnly) { $argv += '--view-only' }
Write-Host "[3/3] 웹 서버 시작: $probeUrl  (종료: Ctrl+C 또는 창 닫기)" -ForegroundColor Green
$server = Start-Process -FilePath $py.Exe -ArgumentList $argv -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
$null = $server.Handle  # 종료코드를 읽기 위해 핸들 확보 (PS 5.1)
$served = $false
try {
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if ($server.HasExited) { throw "서버가 바로 종료되었습니다 (exit $($server.ExitCode)). 위 오류 메시지를 확인하세요." }
        try { Invoke-WebRequest -UseBasicParsing -Uri "$probeUrl/api/analyses" -TimeoutSec 2 | Out-Null; $ready = $true; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw '서버 응답이 없습니다.' }
    $openUrl = $probeUrl
    if ($Tailscale) {
        Write-Host "      tailscale serve 설정: https://$tsName/  ->  $localUrl"
        & $ts serve --bg --https=443 $localUrl
        if ($LASTEXITCODE -ne 0) {
            Write-Host '      tailscale serve 실패. 위 메시지에 HTTPS 인증서 활성화 링크가 있으면 관리 콘솔에서 켠 뒤 다시 실행하세요.' -ForegroundColor Yellow
        } else {
            $served = $true; $openUrl = "https://$tsName/"
            Write-Host ''
            Write-Host "  다른 기기(tailnet)에서 접속:  https://$tsName/" -ForegroundColor Cyan
            Write-Host "  보고서 예:  https://$tsName/reports/uav7_20260904_030446/report.html" -ForegroundColor Cyan
            Write-Host ''
        }
    }
    if ($Bind) {
        Write-Host ''
        Write-Host "  다른 기기(tailnet)에서 접속:  http://${Bind}:$Port/   (https 아님)" -ForegroundColor Cyan
        Write-Host '  접속이 안 되면 관리자 PowerShell에서 한 번만 방화벽 허용(Tailscale 대역만):' -ForegroundColor Yellow
        Write-Host "    New-NetFirewallRule -DisplayName 'coverage-web $Port (tailscale)' -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress 100.64.0.0/10 -Action Allow"
        Write-Host ''
    }
    if (-not $NoBrowser) { Start-Process $openUrl }
    Wait-Process -Id $server.Id
} finally {
    if ($served) { & $ts serve --https=443 off | Out-Null; Write-Host 'tailscale serve 해제' }
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
