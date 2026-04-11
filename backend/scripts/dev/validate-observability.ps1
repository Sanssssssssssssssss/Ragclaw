[CmdletBinding()]
param(
    [switch]$IncludeFrontendBuild,
    [switch]$SkipStudioSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$repoRoot = Split-Path -Parent $backendDir
$pythonExe = Join-Path $backendDir ".venv\\Scripts\\python.exe"
$langgraphExe = Join-Path $backendDir ".venv\\Scripts\\langgraph.exe"
$frontendDir = Join-Path $repoRoot "src\\frontend"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
}

function Get-NpmCommand {
    foreach ($commandName in @("npm.cmd", "npm")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    throw "npm was not found. Install Node.js LTS or restart VS Code so the updated PATH is available."
}

if (-not (Test-Path $pythonExe)) {
    throw "Python virtual environment not found at $pythonExe"
}

$tests = @(
    "backend.tests.test_harness_chat_integration",
    "backend.tests.test_langgraph_checkpointing",
    "backend.tests.test_hitl_edit_flow",
    "backend.tests.test_recovery_policy",
    "backend.tests.test_mcp_filesystem",
    "backend.tests.test_context_assembler",
    "backend.tests.test_benchmark_runner",
    "backend.tests.test_harness_live_validation",
    "backend.tests.test_otel_tracing",
    "backend.tests.test_model_call_context_trace",
    "backend.tests.test_context_turn_linking"
)

Push-Location $repoRoot
try {
    Invoke-Step "Compile backend" {
        & $pythonExe -m compileall src/backend
        if ($LASTEXITCODE -ne 0) {
            throw "compileall failed with exit code $LASTEXITCODE"
        }
    }

    Invoke-Step "Run focused backend tests" {
        & $pythonExe -m unittest @tests
        if ($LASTEXITCODE -ne 0) {
            throw "focused backend tests failed with exit code $LASTEXITCODE"
        }
    }

    if ($IncludeFrontendBuild) {
        $npmCommand = Get-NpmCommand
        Invoke-Step "Build frontend" {
            Push-Location $frontendDir
            try {
                & $npmCommand run build
                if ($LASTEXITCODE -ne 0) {
                    throw "frontend build failed with exit code $LASTEXITCODE"
                }
            }
            finally {
                Pop-Location
            }
        }
    }

    if (-not $SkipStudioSmoke) {
        if (-not (Test-Path $langgraphExe)) {
            throw "langgraph CLI was not found at $langgraphExe"
        }

        $studioSmoke = @'
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

root = Path(sys.argv[1])
langgraph_exe = Path(sys.argv[2])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

env = dict(**__import__("os").environ)
env["LANGSMITH_TRACING"] = "false"
env["LANGCHAIN_TRACING_V2"] = "false"

proc = subprocess.Popen(
    [
        str(langgraph_exe),
        "dev",
        "--config",
        "langgraph.json",
        "--port",
        str(port),
        "--allow-blocking",
        "--no-browser",
    ],
    cwd=root,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

def wait_for_port(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("langgraph dev did not open its port in time")

def request(method, url, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))

try:
    wait_for_port()
    assistants = request("POST", f"http://127.0.0.1:{port}/assistants/search", {})
    assistant = assistants[0]
    thread = request("POST", f"http://127.0.0.1:{port}/threads", {})
    run = request(
        "POST",
        f"http://127.0.0.1:{port}/threads/{thread['thread_id']}/runs/wait",
        {
            "assistant_id": assistant["assistant_id"],
            "input": {"messages": [{"role": "user", "content": "Studio validation smoke."}]},
        },
    )
    assert run.get("messages"), "run response did not include assistant messages"
    assert run.get("input_preview"), "run response did not include input_preview"
    assert run.get("output_preview"), "run response did not include output_preview"
    print(json.dumps({"assistant_id": assistant["assistant_id"], "thread_id": thread["thread_id"], "path_kind": run.get("path_kind", "")}))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
'@

        Invoke-Step "Run Studio smoke" {
            $studioSmoke | & $pythonExe - $repoRoot $langgraphExe
            if ($LASTEXITCODE -ne 0) {
                throw "Studio smoke failed with exit code $LASTEXITCODE"
            }
        }
    }

    Write-Host "Observability validation passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
