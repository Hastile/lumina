$ErrorActionPreference = 'Stop'
$engineRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\engine'))
$python = Join-Path $engineRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Lumina Python runtime missing' }
if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
Start-Process -FilePath $python -ArgumentList @('-m', 'lumina.mobile_monitor', '--host', '127.0.0.1', '--port', '8767') -WorkingDirectory $engineRoot -WindowStyle Hidden
