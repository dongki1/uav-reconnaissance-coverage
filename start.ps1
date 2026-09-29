$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& ./.venv/Scripts/python.exe -X utf8 uav_web/server.py --port 8766
