param(
    [string]$Version = "16.13",
    [string]$DownloadUrl = "https://sbp.enterprisedb.com/getfile.jsp?fileid=1260112"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")).Path
$downloadsDir = Join-Path $repoRoot "artifacts\\downloads"
$zipPath = Join-Path $downloadsDir "postgresql-$Version-windows-x64-binaries.zip"
$extractRoot = Join-Path $repoRoot "artifacts\\postgres16_zip"

New-Item -ItemType Directory -Force $downloadsDir | Out-Null

if (-not (Test-Path $zipPath)) {
    Write-Host "Downloading PostgreSQL $Version binary archive..."
    curl.exe -L $DownloadUrl -o $zipPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download PostgreSQL binaries from $DownloadUrl"
    }
}

if (-not (Test-Path (Join-Path $extractRoot "pgsql\\bin\\postgres.exe"))) {
    Write-Host "Extracting PostgreSQL binaries..."
    Remove-Item $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force
}

Write-Host "PostgreSQL binaries are ready at $extractRoot"
