$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    chcp 65001 > $null
    $Utf8NoBom = [System.Text.UTF8Encoding]::new()
    [Console]::InputEncoding = $Utf8NoBom
    [Console]::OutputEncoding = $Utf8NoBom
    $OutputEncoding = $Utf8NoBom
} catch {
    # Best effort for older PowerShell hosts.
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SmokeTest = Join-Path $ProjectRoot "scripts\smoke_test.ps1"

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Script
    )

    Write-Host ""
    Write-Host "== $Name =="
    & $Script
    if ($LASTEXITCODE -ne 0) {
        Fail "$Name failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    Fail "Missing .venv\Scripts\python.exe. Run .\install.ps1 or create the project virtual environment first."
}

Set-Location $ProjectRoot

& $Python -m pytest --version *> $null
if ($LASTEXITCODE -ne 0) {
    Fail 'Missing pytest. Install dev tools with: .\.venv\Scripts\python.exe -m pip install -e ".[dev]"'
}

& $Python -m ruff --version *> $null
if ($LASTEXITCODE -ne 0) {
    Fail 'Missing ruff. Install dev tools with: .\.venv\Scripts\python.exe -m pip install -e ".[dev]"'
}

if (-not (Test-Path -LiteralPath $SmokeTest)) {
    Fail "Missing scripts\smoke_test.ps1."
}

Invoke-Checked "ruff" {
    & $Python -m ruff check --no-cache "src/tools/ffmpeg_locator.py" "tests"
}

Invoke-Checked "pytest" {
    & $Python -m pytest
}

Invoke-Checked "smoke test" {
    & $SmokeTest
}

Write-Host ""
Write-Host "Development checks completed."
