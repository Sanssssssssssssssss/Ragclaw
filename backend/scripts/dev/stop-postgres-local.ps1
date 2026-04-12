param(
    [int]$Port = 35432
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")).Path
$pgRoot = Join-Path $repoRoot "artifacts\\postgres16_zip\\pgsql"
$pgData = Join-Path $repoRoot "artifacts\\pgdata_zip"
$pidFile = Join-Path $repoRoot "artifacts\\postgres_logs\\postgres-local.pid"
$pgCtlExe = Join-Path $pgRoot "bin\\pg_ctl.exe"

$env:PATH = (Join-Path $pgRoot "bin") + ";" + $env:PATH

if (Test-Path $pgCtlExe) {
    & $pgCtlExe stop -D $pgData -m fast | Out-Null
}

if (Test-Path $pidFile) {
    $processId = Get-Content $pidFile | Select-Object -First 1
    if ($processId) {
        Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "PostgreSQL stop signal sent for port $Port"
