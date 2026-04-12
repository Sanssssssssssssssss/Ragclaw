param(
    [int]$Port = 35432
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")).Path
$pgRoot = Join-Path $repoRoot "artifacts\\postgres16_zip\\pgsql"
$pgData = Join-Path $repoRoot "artifacts\\pgdata_zip"
$logDir = Join-Path $repoRoot "artifacts\\postgres_logs"
$pidFile = Join-Path $logDir "postgres-local.pid"
$postgresExe = Join-Path $pgRoot "bin\\postgres.exe"
$initdbExe = Join-Path $pgRoot "bin\\initdb.exe"
$pgIsReadyExe = Join-Path $pgRoot "bin\\pg_isready.exe"

if (-not (Test-Path $postgresExe)) {
    throw "PostgreSQL binaries not found. Run backend/scripts/dev/bootstrap-postgres-binaries.ps1 first."
}

New-Item -ItemType Directory -Force $logDir | Out-Null

$env:PATH = (Join-Path $pgRoot "bin") + ";" + $env:PATH

if (-not (Test-Path (Join-Path $pgData "PG_VERSION"))) {
    Remove-Item $pgData -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $pgData | Out-Null
    & $initdbExe -D $pgData -A trust -U postgres --locale C --encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw "initdb failed for $pgData"
    }
}

try {
    & $pgIsReadyExe -h 127.0.0.1 -p $Port -U postgres -d postgres | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PostgreSQL is already listening on 127.0.0.1:$Port"
        exit 0
    }
} catch {
}

$logPath = Join-Path $logDir "postgres-local.log"
$stdoutPath = Join-Path $logDir "postgres-local.stdout.log"
$stderrPath = Join-Path $logDir "postgres-local.stderr.log"
$process = Start-Process -FilePath $postgresExe -ArgumentList "-D", $pgData, "-p", $Port, "-c", "listen_addresses=127.0.0.1" -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
Set-Content -Path $pidFile -Value $process.Id

for ($i = 0; $i -lt 60; $i++) {
    & $pgIsReadyExe -h 127.0.0.1 -p $Port -U postgres -d postgres | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PostgreSQL is ready at 127.0.0.1:$Port"
        Write-Host "DSN: postgresql://postgres@127.0.0.1:$Port/postgres"
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

throw "PostgreSQL did not become ready within the timeout. Check $stdoutPath and $stderrPath"
