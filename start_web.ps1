$ErrorActionPreference = "Stop"

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

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogPath = Join-Path $ProjectRoot "logs\web_server.log"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "CineSub Studio could not find the project virtual environment." -ForegroundColor Red
    Write-Host "Missing: $Python"
    Write-Host ""
    Write-Host "First run setup:"
    Write-Host "  .\install.ps1"
    Write-Host "Then start again:"
    Write-Host "  .\start_web.ps1"
    Write-Host ""
    Write-Host "This script only uses the project .venv and does not modify system PATH or PowerShell profile."
    exit 1
}

try {
    & $Python -B (Join-Path $ProjectRoot "start_app.py") @args
    $ExitCode = $LASTEXITCODE
} catch {
    Write-Host "Failed to start CineSub Studio Web launcher." -ForegroundColor Red
    Write-Host "Python: $Python"
    Write-Host "Error: $($_.Exception.Message)"
    Write-Host "If the Web server wrote a log, check: $LogPath"
    exit 1
}

if ($ExitCode -ne 0) {
    Write-Host "CineSub Studio Web launcher exited with code $ExitCode." -ForegroundColor Red
    Write-Host "Python: $Python"
    Write-Host "Log path: $LogPath"
    Write-Host "Common next steps:"
    Write-Host "  1. Run .\install.ps1 if dependencies are missing."
    Write-Host "  2. Open Web diagnostics after startup succeeds: http://127.0.0.1:7860/api/runtime/diagnostics"
    Write-Host "  3. Check FFmpeg setup with: .\scripts\download_ffmpeg.ps1"
}
exit $ExitCode
