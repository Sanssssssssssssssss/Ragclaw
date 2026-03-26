param(
    [switch]$DryRun,
    [switch]$InstallIfMissing
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
$frontendNodeModules = Join-Path $frontendDir "node_modules"

function Ensure-BackendEnvironment {
    if (Test-Path $backendPython) {
        return
    }

    if (-not $InstallIfMissing) {
        throw "Missing backend/.venv. Run the one-time setup in LOCAL_DEV.md, or use start-dev.ps1 -InstallIfMissing."
    }

    Write-Host "[setup] Creating backend virtual environment and installing dependencies..." -ForegroundColor Cyan
    Push-Location $backendDir
    try {
        py -3.13 -m venv .venv
        & $backendPython -m pip install -r requirements.txt
    }
    finally {
        Pop-Location
    }
}

function Ensure-FrontendEnvironment {
    if (Test-Path $frontendNodeModules) {
        return
    }

    if (-not $InstallIfMissing) {
        throw "Missing frontend/node_modules. Run the one-time setup in LOCAL_DEV.md, or use start-dev.ps1 -InstallIfMissing."
    }

    Write-Host "[setup] Installing frontend dependencies..." -ForegroundColor Cyan
    Push-Location $frontendDir
    try {
        npm install
    }
    finally {
        Pop-Location
    }
}

Ensure-BackendEnvironment
Ensure-FrontendEnvironment

$backendCommand = @"
Set-Location '$backendDir'
if (-not (Test-Path '.env')) {
  Write-Host '[tip] backend/.env is missing. Copy .env.example to .env and add your Kimi API key.' -ForegroundColor Yellow
}
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8004 --reload
"@

$frontendCommand = @"
Set-Location '$frontendDir'
npm run dev
"@

if ($DryRun) {
    Write-Host "[dry-run] Backend command:" -ForegroundColor Green
    Write-Host $backendCommand
    Write-Host "[dry-run] Frontend command:" -ForegroundColor Green
    Write-Host $frontendCommand
    exit 0
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand | Out-Null
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand | Out-Null

Write-Host ""
Write-Host "Development services started:" -ForegroundColor Green
Write-Host "- Frontend: http://127.0.0.1:3000"
Write-Host "- Backend: http://127.0.0.1:8004"
Write-Host "- Health: http://127.0.0.1:8004/health"
Write-Host ""
Write-Host "To switch to Kimi, edit backend/.env." -ForegroundColor Yellow
