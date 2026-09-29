param([string]$PythonExe = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $PythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11~3.12 설치 및 venv 생성을 확인하세요.' }
}
& ./.venv/Scripts/python.exe -m pip --version 2>$null
if ($LASTEXITCODE -eq 0) {
    & ./.venv/Scripts/python.exe -m pip install -r uav_web/requirements.txt
} else {
    & $PythonExe -m pip --python .venv/Scripts/python.exe install -r uav_web/requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw '패키지 설치 실패' }
& ./.venv/Scripts/python.exe uav_web/download_model.py
if ($LASTEXITCODE -ne 0) { throw '모델 다운로드 실패' }
Write-Host '설치 완료. start.ps1을 실행하세요.'
