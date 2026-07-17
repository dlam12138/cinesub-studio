# v0.5 Windows Zero-Config Installer Preview — Runtime Collector
#
# Stages and verifies bundled runtime components:
#   - Complete Python runtime (from tools/python)
#   - Python dependencies (from .venv site-packages)
#   - FFmpeg binaries
#   - Optional CUDA runtime DLLs
#
# electron-builder copies the validated staging directory produced here.

param(
    [switch]$DryRun,
    [switch]$RequireCuda
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$components = @{
    "Portable Python" = (Join-Path $root "tools\python")
    "Python dependencies (.venv)" = (Join-Path $root ".venv\Lib\site-packages")
    "FFmpeg" = (Join-Path $root "tools\ffmpeg")
}

if ($RequireCuda) {
    $components["CUDA runtime (required)"] = Join-Path $root "tools\cuda"
}

foreach ($name in $components.Keys) {
    $path = $components[$name]
    if (Test-Path $path) {
        $size = (Get-ChildItem $path -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        $mb = [math]::Round($size / 1MB, 1)
        Write-Host "$name`: $path (${mb} MB)" -ForegroundColor Green
    } else {
        Write-Host "$name`: $path NOT FOUND" -ForegroundColor Red
    }
}

# License awareness check
$notices = Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md"
if (-not (Test-Path $notices)) {
    Write-Warning "THIRD_PARTY_NOTICES.md not found at $notices"
}

if ($DryRun) {
    Write-Host "Dry-run: no files copied."
    exit 0
}

$portablePython = Join-Path $root "tools\python\python.exe"
$preparer = Join-Path $PSScriptRoot "prepare_runtime.py"
$destination = Join-Path $PSScriptRoot "runtime"
if (-not (Test-Path $portablePython)) {
    throw "Portable Python is required at $portablePython"
}

$arguments = @(
    "-B",
    $preparer,
    "--project-root", $root,
    "--destination", $destination
)
if ($RequireCuda) {
    $arguments += "--require-cuda"
}

& $portablePython @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Runtime preparation failed with exit code $LASTEXITCODE"
}
