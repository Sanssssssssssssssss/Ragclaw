param(
    [int]$Port = 8015,
    [int]$HealthTimeoutSeconds = 90,
    [double]$CaseDelaySeconds = 3,
    [double]$RateLimitRetryBaseSeconds = 4,
    [int]$MaxRateLimitRetries = 2,
    [string]$Suite = "full",
    [string]$Module = "",
    [string]$RagSubtype = "",
    [string]$QuestionType = "",
    [string]$Modalities = "",
    [string]$DifficultyMin = "",
    [string]$DifficultyMax = "",
    [string]$SamplePerType = "",
    [switch]$KeepSessions
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$backendDir = Join-Path $root "backend"
$pythonExe = Join-Path $backendDir ".venv\\Scripts\\python.exe"
$backendStartScript = Join-Path $root "scripts\\dev\\start-backend-dev.ps1"
$powershellExe = Join-Path $env:SystemRoot "System32\\WindowsPowerShell\\v1.0\\powershell.exe"
if (-not (Test-Path $powershellExe)) {
    $powershellExe = "powershell.exe"
}

$baseUrl = "http://127.0.0.1:$Port"
$healthUrl = "$baseUrl/health"

function Test-PortInUse {
    param([int]$TargetPort)

    $listener = netstat -ano -p tcp |
        Select-String -Pattern "LISTENING\s+(\d+)$" |
        ForEach-Object {
            $parts = ($_ -replace "\s+", " ").Trim().Split(" ")
            if ($parts.Length -ge 5 -and $parts[1] -like "*:$TargetPort") {
                [pscustomobject]@{
                    LocalAddress = $parts[1]
                    ProcessId = [int]$parts[4]
                }
            }
        } |
        Select-Object -First 1

    return $listener
}

function Wait-ForHealth {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 3
            if ($response.status -eq "ok") {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 1000
        }
    }

    return $false
}

if (-not (Test-Path $pythonExe)) {
    throw "Missing backend virtual environment at $pythonExe"
}

$existingListener = Test-PortInUse -TargetPort $Port
if ($existingListener) {
    throw "Port $Port is already in use by PID $($existingListener.ProcessId). Please free the port or run with -Port <other>."
}

Write-Host "Starting backend on port $Port..." -ForegroundColor Cyan
$backendProcess = Start-Process `
    -FilePath $powershellExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $backendStartScript, "-Port", "$Port") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

try {
    Write-Host "Waiting for backend health..." -ForegroundColor Cyan
    if (-not (Wait-ForHealth -Url $healthUrl -TimeoutSeconds $HealthTimeoutSeconds)) {
        throw "Backend did not become healthy within $HealthTimeoutSeconds seconds."
    }

    Write-Host "Running backend benchmarks..." -ForegroundColor Cyan
    $arguments = @(
        "-m", "benchmarks.runner",
        "--base-url", $baseUrl,
        "--suite", $Suite,
        "--case-delay-seconds", "$CaseDelaySeconds",
        "--rate-limit-retry-base-seconds", "$RateLimitRetryBaseSeconds",
        "--max-rate-limit-retries", "$MaxRateLimitRetries"
    )
    if ($Module) {
        $arguments += @("--module", $Module)
    }
    if ($RagSubtype) {
        $arguments += @("--rag-subtype", $RagSubtype)
    }
    if ($QuestionType) {
        $arguments += @("--question-type", $QuestionType)
    }
    if ($Modalities) {
        $arguments += @("--modalities", $Modalities)
    }
    if ($DifficultyMin) {
        $arguments += @("--difficulty-min", $DifficultyMin)
    }
    if ($DifficultyMax) {
        $arguments += @("--difficulty-max", $DifficultyMax)
    }
    if ($SamplePerType) {
        $arguments += @("--sample-per-type", $SamplePerType)
    }
    if ($KeepSessions) {
        $arguments += "--keep-sessions"
    }

    Push-Location $backendDir
    try {
        & $pythonExe @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Benchmark runner exited with code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Write-Host "Stopping backend PID $($backendProcess.Id)..." -ForegroundColor Yellow
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
