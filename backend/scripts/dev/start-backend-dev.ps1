param(
    [int]$Port = 8015
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$projectRoot = Split-Path -Parent $backendDir

Set-Location $projectRoot

if (-not (Test-Path (Join-Path $backendDir ".env"))) {
    Write-Host "[tip] backend/.env is missing. Copy .env.example to .env and add your Kimi API key." -ForegroundColor Yellow
}

& (Join-Path $backendDir ".venv\\Scripts\\python.exe") -m uvicorn src.backend.api.app:app --host 127.0.0.1 --port $Port
