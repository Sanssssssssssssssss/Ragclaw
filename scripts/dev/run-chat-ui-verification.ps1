Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$backendScript = Join-Path $root "scripts\\dev\\start-backend-dev.ps1"
$frontendScript = Join-Path $root "scripts\\dev\\start-frontend-dev.ps1"
$frontendDir = Join-Path $root "frontend"

$backend = $null
$frontend = $null

try {
    $backend = Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $backendScript, "-Port", "8015" -PassThru
    $frontend = Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $frontendScript, "-ApiBaseUrl", "http://127.0.0.1:8015/api" -PassThru

    $backendReady = $false
    for ($index = 0; $index -lt 180; $index++) {
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:8015/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $backendReady = $true
            break
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    if (-not $backendReady) {
        throw "Backend did not become ready on 8015."
    }

    $frontendReady = $false
    for ($index = 0; $index -lt 120; $index++) {
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $frontendReady = $true
            break
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    if (-not $frontendReady) {
        throw "Frontend did not become ready on 3000."
    }

    Push-Location $frontendDir
    try {
        npm run verify:chat-ui
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($frontend) {
        Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
    }
    if ($backend) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    }
}
