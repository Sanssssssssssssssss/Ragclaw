param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8015/api"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$projectRoot = Split-Path -Parent $backendDir
$frontendDir = Join-Path $projectRoot "src\\frontend"

Set-Location $frontendDir
$env:NEXT_PUBLIC_API_BASE_URL = $ApiBaseUrl

npm run dev
