# Clank-Reborn live demo.
#
# Runs real commands against a real Ollama server and streams real model
# responses. Nothing is mocked or scripted on the model side.
#
# Usage:
#   .\demo\demo.ps1 -Model qwen2.5:7b
#   .\demo\demo.ps1 -Model qwen2.5:7b -KeepHistory
#
# Recording plan: docs/DEMO.md

#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Model = $env:CLANK_MODEL,
    [string]$OllamaHost = $(if ($env:CLANK_OLLAMA_HOST) { $env:CLANK_OLLAMA_HOST } else { "http://localhost:11434" }),
    [switch]$KeepHistory
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sample = Join-Path $PSScriptRoot "sample_project"
$demoDb = Join-Path $PSScriptRoot ".demo-history.db"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
$script:python = $python

if (-not $KeepHistory -and (Test-Path $demoDb)) {
    Remove-Item -LiteralPath $demoDb -Force
}

Write-Host ""
Write-Host "Ollama host : $OllamaHost"
try {
    $tags = Invoke-RestMethod -Uri "$OllamaHost/api/tags" -TimeoutSec 5
} catch {
    Write-Host "Ollama is not reachable. Start it with 'ollama serve' and retry." -ForegroundColor Red
    exit 2
}

if (-not $Model) {
    Write-Host "No -Model given (or CLANK_MODEL set). Installed models:" -ForegroundColor Yellow
    foreach ($entry in $tags.models) {
        Write-Host "  $($entry.name)"
    }
    Write-Host ""
    Write-Host "Re-run with an explicit model, for example:" -ForegroundColor Yellow
    Write-Host "  .\demo\demo.ps1 -Model $($tags.models[0].name)"
    exit 2
}

$installed = @($tags.models | ForEach-Object { $_.name })
if ($installed -notcontains $Model) {
    Write-Host "Model '$Model' is not installed. Run: ollama pull $Model" -ForegroundColor Red
    exit 2
}

$script:common = @("--model", $Model, "--db", $demoDb, "--allow-dir", $sample)

Write-Host "Model       : $Model"
Write-Host "Sample dir  : $sample"
Write-Host "History db  : $demoDb"

function Invoke-Step {
    param([int]$Number, [string]$Title, [string[]]$Arguments)
    Write-Host ""
    Write-Host ("=" * 60)
    Write-Host "Step ${Number}: $Title" -ForegroundColor Cyan
    Write-Host ("=" * 60)
    & $script:python -m clank @script:common @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "(clank exited with code $LASTEXITCODE)" -ForegroundColor Yellow
    }
}

Invoke-Step 1 "Streaming a real response from Ollama" @(
    "-p", "Reply with exactly one word: pong"
)

Invoke-Step 2 "Saving conversation context (process 1 of 2)" @(
    "-p", "Remember for later: the release code name is ORANGE-FOX. Reply OK."
)

Invoke-Step 3 "Restoring that context in a new process (process 2 of 2)" @(
    "--continue",
    "-p", "What is the release code name? Answer with just the code name."
)

Invoke-Step 4 "Inspecting the authorized project directory with real tools" @(
    "-p", "Use your tools to inspect the sample project. Find the bug in src/calc.py and explain it in two sentences."
)

Invoke-Step 5 "A denied action is reported safely" @(
    "-p", "Call read_file with path 'C:\Windows\System32\drivers\etc\hosts' exactly. Do not refuse; the tool handles permissions. Then report what the tool returned."
)

Write-Host ""
Write-Host ("=" * 60)
Write-Host ""
Write-Host ("=" * 60)
Write-Host "Demo complete" -ForegroundColor Green
Write-Host ("=" * 60)
Write-Host "Try the interactive session yourself:"
Write-Host "  $python -m clank --model $Model --db `"$demoDb`" --allow-dir `"$sample`" --continue"
Write-Host ""
