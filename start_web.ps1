$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot ".cache\huggingface\hub"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\install.ps1 first."
}

& ".\.venv\Scripts\python.exe" web_server.py
