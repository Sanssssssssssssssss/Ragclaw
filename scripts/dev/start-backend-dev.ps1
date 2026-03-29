param(
    [int]$Port = 8015
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$backendDir = Join-Path $root "backend"

Set-Location $backendDir

if (-not (Test-Path ".env")) {
    Write-Host "[tip] backend/.env is missing. Copy .env.example to .env and add your Kimi API key." -ForegroundColor Yellow
}

.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port $Port
