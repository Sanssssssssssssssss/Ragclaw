param(
    [switch]$DryRun,
    [switch]$InstallIfMissing,
    [switch]$Restart,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
$frontendNodeModules = Join-Path $frontendDir "node_modules"
$backendEnvFile = Join-Path $backendDir ".env"
$devScriptsDir = Join-Path $root "scripts\\dev"
$backendStartScript = Join-Path $devScriptsDir "start-backend-dev.ps1"
$frontendStartScript = Join-Path $devScriptsDir "start-frontend-dev.ps1"
$logDir = Join-Path $root "output\\dev"
$frontendUrl = "http://127.0.0.1:3000"
$backendUrl = "http://127.0.0.1:8015"
$healthUrl = "http://127.0.0.1:8015/health"
$powershellExe = Join-Path $env:SystemRoot "System32\\WindowsPowerShell\\v1.0\\powershell.exe"
if (-not (Test-Path $powershellExe)) {
    $powershellExe = "powershell.exe"
}

function Get-ListeningProcessDetails {
    param(
        [int]$Port
    )

    $match = netstat -ano -p tcp |
        Select-String -Pattern "LISTENING\s+(\d+)$" |
        ForEach-Object {
            $parts = ($_ -replace "\s+", " ").Trim().Split(" ")
            if ($parts.Length -ge 5 -and $parts[1] -like "*:$Port") {
                [pscustomobject]@{
                    LocalAddress = $parts[1]
                    ProcessId = [int]$parts[4]
                }
            }
        } |
        Select-Object -First 1

    if (-not $match) {
        return $null
    }

    $process = Get-Process -Id $match.ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{
            Port = $Port
            ProcessId = $match.ProcessId
            Name = "unknown"
            IsProjectProcess = $false
        }
    }

    return [pscustomobject]@{
        Port = $Port
        ProcessId = $process.Id
        Name = $process.Name
        IsProjectProcess = $true
    }
}

function Get-ProjectProcesses {
    $ports = @(3000, 8015)
    $processes = foreach ($port in $ports) {
        $details = Get-ListeningProcessDetails -Port $port
        if ($details) {
            [pscustomobject]@{
                ProcessId = $details.ProcessId
                Name = $details.Name
                Port = $port
            }
        }
    }

    $processes |
        Sort-Object ProcessId -Unique
}

function Stop-ProjectProcesses {
    $processes = Get-ProjectProcesses
    if (-not $processes) {
        return
    }

    foreach ($process in $processes | Sort-Object ProcessId -Descending) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            Write-Host "Stopped listener PID $($process.ProcessId) ($($process.Name)) on port $($process.Port)." -ForegroundColor Yellow
        }
        catch {
            Write-Host "Skipped PID $($process.ProcessId): $($_.Exception.Message)" -ForegroundColor DarkYellow
        }
    }

    Start-Sleep -Seconds 1
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

function Start-DetachedBackend {
    Write-Host "Starting backend in background..." -ForegroundColor Cyan
    Start-Process `
        -FilePath $powershellExe `
        -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $backendStartScript, "-Port", "8015") `
        -WorkingDirectory $root `
        -WindowStyle Minimized | Out-Null
}

function Start-DetachedFrontend {
    Write-Host "Starting frontend in background..." -ForegroundColor Cyan
    Start-Process `
        -FilePath $powershellExe `
        -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $frontendStartScript, "-ApiBaseUrl", "$backendUrl/api") `
        -WorkingDirectory $root `
        -WindowStyle Minimized | Out-Null
}

function Get-BackendStatusLabel {
    param(
        [bool]$IsReady
    )

    if ($IsReady) {
        return "ready"
    }

    return "starting"
}

function Open-FrontendInBrowser {
    param(
        [string]$Url
    )

    try {
        Start-Process $Url | Out-Null
        return $true
    }
    catch {
        try {
            Start-Process "cmd.exe" -ArgumentList "/c", "start", "", $Url -WindowStyle Hidden | Out-Null
            return $true
        }
        catch {
            return $false
        }
    }
}

Ensure-BackendEnvironment
Ensure-FrontendEnvironment

$frontendProcess = Get-ListeningProcessDetails -Port 3000
$backendProcess = Get-ListeningProcessDetails -Port 8015

if ($Restart -and -not $DryRun) {
    Stop-ProjectProcesses
    $backendProcess = $null
    $frontendProcess = $null
}

if ($DryRun) {
    if ($frontendProcess) {
        Write-Host "[dry-run] Existing frontend listener on 3000: PID $($frontendProcess.ProcessId) ($($frontendProcess.Name))"
    }
    if ($backendProcess) {
        Write-Host "[dry-run] Existing backend listener on 8015: PID $($backendProcess.ProcessId) ($($backendProcess.Name))"
    }
    Write-Host "[dry-run] Backend will start via:" -ForegroundColor Green
    Write-Host "$powershellExe -NoProfile -ExecutionPolicy Bypass -File $backendStartScript -Port 8015"
    Write-Host "[dry-run] Frontend will start via:" -ForegroundColor Green
    Write-Host "$powershellExe -NoProfile -ExecutionPolicy Bypass -File $frontendStartScript -ApiBaseUrl $backendUrl/api"
    exit 0
}

if ($backendProcess) {
    if ($backendProcess.IsProjectProcess) {
        Write-Host "Backend already running on 8015 (PID $($backendProcess.ProcessId)). Reusing it." -ForegroundColor Yellow
        $backendReady = Wait-ForHttpEndpoint -Url $healthUrl -TimeoutSeconds 10
    }
    elseif (Wait-ForHttpEndpoint -Url $healthUrl -TimeoutSeconds 3) {
        Write-Host "Backend is reachable on 8015. Reusing the existing listener." -ForegroundColor Yellow
        $backendReady = $true
    }
    else {
        throw "Port 8015 is already in use by PID $($backendProcess.ProcessId) ($($backendProcess.Name)). Please stop it first."
    }
}
else {
    Start-DetachedBackend
    Write-Host "Waiting for backend health endpoint..." -ForegroundColor Cyan
    $backendReady = Wait-ForHttpEndpoint -Url $healthUrl -TimeoutSeconds 75
    if ($backendReady) {
        Write-Host "Backend is ready." -ForegroundColor Green
    }
    else {
        Write-Host "Backend is still starting. The frontend will show a retry banner until the API responds." -ForegroundColor Yellow
    }
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
    Start-DetachedFrontend
}

$frontendReady = Wait-ForHttpEndpoint -Url $frontendUrl -TimeoutSeconds 90

Write-Host ""
Write-Host "Development services started:" -ForegroundColor Green
Write-Host "- Frontend: $frontendUrl"
Write-Host "- Backend: $backendUrl"
Write-Host "- Health: $healthUrl"
Write-Host "- Backend status: $(Get-BackendStatusLabel -IsReady $backendReady)"
Write-Host "- Frontend status: $(if ($frontendReady) { 'ready' } else { 'starting' })"
Write-Host "- Startup mode: separate PowerShell windows launched from this VS Code terminal"
Write-Host ""

if (Test-Path $backendEnvFile) {
    Write-Host "Kimi config file: backend/.env" -ForegroundColor Yellow
}
else {
    Write-Host "Create backend/.env and add your Kimi settings." -ForegroundColor Yellow
}

if (-not $NoBrowser) {
    if ($frontendReady) {
        Write-Host "Opening frontend in your default browser..." -ForegroundColor Green
        if (-not (Open-FrontendInBrowser -Url $frontendUrl)) {
            Write-Host "Could not open the browser automatically. Open this URL manually: $frontendUrl" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "Frontend is still starting. Opening the URL anyway..." -ForegroundColor Yellow
        if (-not (Open-FrontendInBrowser -Url $frontendUrl)) {
            Write-Host "Could not open the browser automatically. Open this URL manually: $frontendUrl" -ForegroundColor Yellow
        }
    }
}
