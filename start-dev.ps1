param(
    [switch]$DryRun,
    [switch]$InstallIfMissing,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
$frontendNodeModules = Join-Path $frontendDir "node_modules"
$backendEnvFile = Join-Path $backendDir ".env"
$frontendUrl = "http://127.0.0.1:3000"
$backendUrl = "http://127.0.0.1:8004"
$healthUrl = "http://127.0.0.1:8004/health"

function Get-ListeningProcessDetails {
    param(
        [int]$Port
    )

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) {
        return $null
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{
            Port = $Port
            ProcessId = $connection.OwningProcess
            Name = "unknown"
            CommandLine = ""
            IsProjectProcess = $false
        }
    }

    $commandLine = ""
    if ($null -ne $process.CommandLine) {
        $commandLine = [string]$process.CommandLine
    }
    return [pscustomobject]@{
        Port = $Port
        ProcessId = $process.ProcessId
        Name = $process.Name
        CommandLine = $commandLine
        IsProjectProcess = $commandLine.Contains($root)
    }
}

function Wait-ForHttpEndpoint {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

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

$frontendProcess = Get-ListeningProcessDetails -Port 3000
$backendProcess = Get-ListeningProcessDetails -Port 8004

if ($Restart -and -not $DryRun) {
    if ($backendProcess -and $backendProcess.IsProjectProcess) {
        Stop-Process -Id $backendProcess.ProcessId -Force
        Write-Host "Stopped existing backend process on 8004 (PID $($backendProcess.ProcessId))." -ForegroundColor Yellow
        Start-Sleep -Milliseconds 500
        $backendProcess = $null
    }

    if ($frontendProcess -and $frontendProcess.IsProjectProcess) {
        Stop-Process -Id $frontendProcess.ProcessId -Force
        Write-Host "Stopped existing frontend process on 3000 (PID $($frontendProcess.ProcessId))." -ForegroundColor Yellow
        Start-Sleep -Milliseconds 500
        $frontendProcess = $null
    }
}

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
    if ($frontendProcess) {
        Write-Host "[dry-run] Existing frontend listener on 3000: PID $($frontendProcess.ProcessId) ($($frontendProcess.Name))"
    }
    elseif ($Restart) {
        Write-Host "[dry-run] Frontend will be restarted if a project process is detected on 3000."
    }
    if ($backendProcess) {
        Write-Host "[dry-run] Existing backend listener on 8004: PID $($backendProcess.ProcessId) ($($backendProcess.Name))"
    }
    elseif ($Restart) {
        Write-Host "[dry-run] Backend will be restarted if a project process is detected on 8004."
    }
    Write-Host "[dry-run] Backend command:" -ForegroundColor Green
    Write-Host $backendCommand
    Write-Host "[dry-run] Frontend command:" -ForegroundColor Green
    Write-Host $frontendCommand
    exit 0
}

if ($backendProcess) {
    if ($backendProcess.IsProjectProcess) {
        Write-Host "Backend already running on 8004 (PID $($backendProcess.ProcessId)). Reusing it." -ForegroundColor Yellow
    }
    elseif (Wait-ForHttpEndpoint -Url $healthUrl -TimeoutSeconds 3) {
        Write-Host "Backend is reachable on 8004. Reusing the existing listener." -ForegroundColor Yellow
    }
    else {
        throw "Port 8004 is already in use by PID $($backendProcess.ProcessId) ($($backendProcess.Name)). Please stop it first."
    }
}
else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand | Out-Null
}

if ($frontendProcess) {
    if ($frontendProcess.IsProjectProcess) {
        Write-Host "Frontend already running on 3000 (PID $($frontendProcess.ProcessId)). Reusing it." -ForegroundColor Yellow
    }
    elseif (Wait-ForHttpEndpoint -Url $frontendUrl -TimeoutSeconds 3) {
        Write-Host "Frontend is reachable on 3000. Reusing the existing listener." -ForegroundColor Yellow
    }
    else {
        throw "Port 3000 is already in use by PID $($frontendProcess.ProcessId) ($($frontendProcess.Name)). Please stop it first."
    }
}
else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand | Out-Null
}

Write-Host ""
Write-Host "Development services started:" -ForegroundColor Green
Write-Host "- Frontend: $frontendUrl"
Write-Host "- Backend: $backendUrl"
Write-Host "- Health: $healthUrl"
Write-Host ""
if (Test-Path $backendEnvFile) {
    Write-Host "Kimi config file: backend/.env" -ForegroundColor Yellow
}
else {
    Write-Host "Create backend/.env and add your Kimi settings." -ForegroundColor Yellow
}

if (Wait-ForHttpEndpoint -Url $frontendUrl -TimeoutSeconds 20) {
    Write-Host "Opening frontend in your default browser..." -ForegroundColor Green
    Start-Process $frontendUrl
}
else {
    Write-Host "Frontend was not reachable yet, so the browser was not opened automatically." -ForegroundColor Yellow
}
