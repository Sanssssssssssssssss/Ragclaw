[CmdletBinding()]
param(
    [ValidateSet("dev", "up")]
    [string]$Mode = "dev",
    [int]$Port = 2024,
    [switch]$NoBrowser,
    [switch]$StrictNonBlocking,
    [string]$ProjectName = "",
    [switch]$EnableConsoleTracing,
    [string]$OtlpEndpoint = ""
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")).Path
$pythonExe = Join-Path $repoRoot "backend\\.venv\\Scripts\\python.exe"
$langgraphExe = Join-Path $repoRoot "backend\\.venv\\Scripts\\langgraph.exe"
$envFile = Join-Path $repoRoot "backend\\.env"

function Get-EnvValueFromFile {
    param(
        [string]$FilePath,
        [string[]]$Keys
    )

    if (-not (Test-Path $FilePath)) {
        return $null
    }

    foreach ($line in Get-Content -Path $FilePath) {
        $trimmed = ($line -as [string]).Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        if ($Keys -notcontains $key) {
            continue
        }
        $value = $parts[1].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($value) {
            return $value
        }
    }

    return $null
}

if (-not (Test-Path $pythonExe)) {
    throw "Backend virtual environment not found at $pythonExe"
}

if (-not (Test-Path $langgraphExe)) {
    & $pythonExe -m pip install -U "langgraph-cli[inmem]" | Out-Host
    if (-not (Test-Path $langgraphExe)) {
        throw "langgraph CLI is not installed in backend/.venv"
    }
}

$langsmithKey = if ($env:LANGSMITH_API_KEY) { $env:LANGSMITH_API_KEY } else { Get-EnvValueFromFile -FilePath $envFile -Keys @("LANGSMITH_API_KEY","LANGCHAIN_API_KEY") }
if ($langsmithKey) {
    $env:LANGSMITH_API_KEY = $langsmithKey
}

if (-not $env:LANGSMITH_TRACING -and -not (Get-EnvValueFromFile -FilePath $envFile -Keys @("LANGSMITH_TRACING","LANGCHAIN_TRACING_V2"))) {
    $env:LANGSMITH_TRACING = "true"
}

if (-not $env:LANGSMITH_API_KEY) {
    throw "LangSmith Studio requires LANGSMITH_API_KEY. Add it to backend/.env or set LANGCHAIN_API_KEY as a compatible fallback."
}

$resolvedProjectName = $ProjectName
if (-not $resolvedProjectName) {
    $resolvedProjectName = if ($env:RAGCLAW_STUDIO_LANGSMITH_PROJECT) {
        $env:RAGCLAW_STUDIO_LANGSMITH_PROJECT
    } else {
        Get-EnvValueFromFile -FilePath $envFile -Keys @("RAGCLAW_STUDIO_LANGSMITH_PROJECT")
    }
}
if (-not $resolvedProjectName) {
    $resolvedProjectName = "Ragclaw Studio"
}
$env:LANGSMITH_PROJECT = $resolvedProjectName
$env:LANGCHAIN_PROJECT = $resolvedProjectName
$env:OTEL_SERVICE_NAME = "ragclaw-langgraph-studio"

if ($EnableConsoleTracing) {
    $env:RAGCLAW_OTEL_ENABLED = "1"
    $env:RAGCLAW_OTEL_CONSOLE_EXPORTER = "1"
}

if ($OtlpEndpoint) {
    $env:RAGCLAW_OTEL_ENABLED = "1"
    $env:OTEL_EXPORTER_OTLP_ENDPOINT = $OtlpEndpoint
}

$args = @($Mode, "--config", "langgraph.json", "--port", "$Port")
if ($Mode -eq "dev" -and -not $StrictNonBlocking) {
    $args += "--allow-blocking"
}
if ($NoBrowser) {
    $args += "--no-browser"
}

Push-Location $repoRoot
try {
    & $langgraphExe @args
    if ($LASTEXITCODE -ne 0) {
        throw "langgraph $Mode failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
