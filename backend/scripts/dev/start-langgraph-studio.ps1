[CmdletBinding()]
param(
    [ValidateSet("dev", "up")]
    [string]$Mode = "dev",
    [int]$Port = 2024,
    [switch]$NoBrowser
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")).Path
$pythonExe = Join-Path $repoRoot "backend\\.venv\\Scripts\\python.exe"
$langgraphExe = Join-Path $repoRoot "backend\\.venv\\Scripts\\langgraph.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Backend virtual environment not found at $pythonExe"
}

if (-not (Test-Path $langgraphExe)) {
    & $pythonExe -m pip install -U "langgraph-cli[inmem]" | Out-Host
    if (-not (Test-Path $langgraphExe)) {
        throw "langgraph CLI is not installed in backend/.venv"
    }
}

$args = @($Mode, "--config", "langgraph.json", "--port", "$Port")
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
