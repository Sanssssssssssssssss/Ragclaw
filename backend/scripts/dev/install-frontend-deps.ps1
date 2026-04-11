Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$projectRoot = Split-Path -Parent $backendDir
$frontendDir = Join-Path $projectRoot "src\\frontend"

function Get-NpmCommand {
    $candidates = @()

    foreach ($commandName in @("npm.cmd", "npm")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "nodejs\\npm.cmd")
    }

    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($programFilesX86) {
        $candidates += (Join-Path $programFilesX86 "nodejs\\npm.cmd")
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "npm was not found. Install Node.js LTS or restart VS Code so the updated PATH is picked up."
}

function Add-NodeDirectoryToPath {
    param(
        [string]$NpmCommand
    )

    $nodeDir = Split-Path -Parent $NpmCommand
    $pathEntries = @($env:Path -split ";" | Where-Object { $_ })
    if ($pathEntries -notcontains $nodeDir) {
        $env:Path = "$nodeDir;$env:Path"
    }
}

Set-Location $frontendDir
$npmCommand = Get-NpmCommand
Add-NodeDirectoryToPath -NpmCommand $npmCommand
& $npmCommand install
