$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\install.ps1 first."
}

& $Python -B (Join-Path $ProjectRoot "src\tools\analyze_subtitles_workflow.py") @args
exit $LASTEXITCODE
