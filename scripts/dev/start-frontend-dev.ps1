param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8015/api"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$frontendDir = Join-Path $root "frontend"

Set-Location $frontendDir
$env:NEXT_PUBLIC_API_BASE_URL = $ApiBaseUrl

npm run dev
