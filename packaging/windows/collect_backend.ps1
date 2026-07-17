# v0.5 Windows Zero-Config Installer Preview — Backend Collector
#
# Verifies that the backend source tree contains the files needed at runtime.
# In v0.5, electron-builder extraResources handles the actual copy; this script
# is a pre-flight checklist.

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$required = @(
    "src\core\transcribe.py",
    "src\core\subtitle_translate.py",
    "src\core\quality_checker.py",
    "src\core\subtitle_model.py",
    "src\pipeline\batch_worker.py",
    "src\config\provider_store.py",
    "src\config\language_profile_store.py",
    "src\web\web_server.py",
    "src\web\runtime_api.py",
    "src\web\pipeline_api.py",
    "src\web\job_api.py",
    "src\tools\runtime_env.py",
    "src\tools\runtime_paths.py",
    "src\tools\ffmpeg_locator.py",
    "web\index.html",
    "start_app.py",
    "start_web.ps1"
)

$missing = @()
foreach ($rel in $required) {
    $full = Join-Path $root $rel
    if (-not (Test-Path $full)) {
        $missing += $rel
    }
}

if ($missing.Count -gt 0) {
    Write-Host "Missing required backend files:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Red }
    exit 1
}

Write-Host "Backend source tree check passed. Required files present: $($required.Count)" -ForegroundColor Green

if ($DryRun) {
    Write-Host "Dry-run: no files copied."
}
