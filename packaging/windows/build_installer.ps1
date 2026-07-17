# v0.5 Windows Zero-Config Installer Preview — Build Orchestrator
#
# This script verifies prerequisites, stages the Electron desktop shell,
# and invokes electron-builder to produce the NSIS installer.
#
# Prerequisites (checked, not automatically downloaded):
#   - Node.js and npm
#   - desktop/node_modules installed (npm install in desktop/)
#   - tools/python complete portable Python 3.12 runtime
#   - Project .venv with all Python dependencies (dependency source only)
#   - tools/ffmpeg/bin/ffmpeg.exe and ffprobe.exe
#   - Complete tools/cuda/ runtime; bundled for automatic GPU/CPU selection
#
# Usage:
#   .\packaging\windows\build_installer.ps1
#   .\packaging\windows\build_installer.ps1 -SkipPreCheck
#   .\packaging\windows\build_installer.ps1 -OnlyUnpacked
#   .\packaging\windows\build_installer.ps1
#   .\packaging\windows\build_installer.ps1 -OnlyUnpacked
#   .\packaging\windows\build_installer.ps1 -OutputDir artifacts\unified

param(
    [switch]$SkipPreCheck,
    [switch]$OnlyUnpacked,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$desktop = Join-Path $root "desktop"
$VersionPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VersionPython)) { throw "Missing project Python for version validation: $VersionPython" }
& $VersionPython -B (Join-Path $root "src\tools\versioning.py") check
if ($LASTEXITCODE -ne 0) { throw "Release version consumers do not match VERSION." }

$Flavor = "unified"

function Write-Info($msg) { Write-Host "[build] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[build] $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "[build] $msg" -ForegroundColor Red }

function Test-Prereqs {
    $ok = $true

    # Node / npm
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Err "npm not found. Install Node.js first."
        $ok = $false
    }

    # desktop node_modules
    if (-not (Test-Path (Join-Path $desktop "node_modules\electron\package.json"))) {
        Write-Err "desktop/node_modules not found. Run: cd desktop; npm install"
        $ok = $false
    }

    # Portable Python plus dependency source
    $portablePython = Join-Path $root "tools\python\python.exe"
    if (-not (Test-Path $portablePython)) {
        Write-Err "Portable Python not found at $portablePython. Import the Python 3.12 runtime first."
        $ok = $false
    }

    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Err ".venv not found at $venvPython. Run install.ps1 first."
        $ok = $false
    }

    # FFmpeg
    $ffmpeg = Join-Path $root "tools\ffmpeg\bin\ffmpeg.exe"
    if (-not (Test-Path $ffmpeg)) {
        Write-Err "FFmpeg not found at $ffmpeg. Place or download FFmpeg first."
        $ok = $false
    }

    # The unified offline installer always carries CUDA and falls back to CPU
    # at runtime when a compatible NVIDIA driver is unavailable.
    $cublas = Join-Path $root "tools\cuda\cublas64_12.dll"
    $cudnn = Get-ChildItem -Path (Join-Path $root "tools\cuda") -Filter "cudnn*_9.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not (Test-Path $cublas)) {
        Write-Err "CUDA cublas64_12.dll not found in tools/cuda/."
        $ok = $false
    }
    if (-not $cudnn) {
        Write-Err "CUDA cudnn*_9.dll not found in tools/cuda/."
        $ok = $false
    }

    # faster-whisper / ctranslate2 importability
    try {
        $env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
        & $venvPython -B -c "import faster_whisper, ctranslate2" 2>$null
        if ($LASTEXITCODE -ne 0) { throw }
    } catch {
        Write-Warn "faster-whisper or ctranslate2 may not be importable in .venv. Unified runtime validation might fail."
    }

    return $ok
}

# ── Main ────────────────────────────────────────────────────────────────────

Push-Location $root

try {
    if (-not $SkipPreCheck) {
        if (-not (Test-Prereqs)) {
            throw "Prerequisite check failed. Fix the issues above and retry."
        }
    }

    $runtimeCollector = Join-Path $PSScriptRoot "collect_runtime.ps1"
    & $runtimeCollector -RequireCuda
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime collector failed with exit code $LASTEXITCODE"
    }

    $resolvedOutput = if ($OutputDir -and [System.IO.Path]::IsPathRooted($OutputDir)) {
        [System.IO.Path]::GetFullPath($OutputDir)
    } elseif ($OutputDir) {
        [System.IO.Path]::GetFullPath((Join-Path $root $OutputDir))
    } else {
        Join-Path $desktop "release\unified"
    }

    $package = Get-Content -Raw -Encoding UTF8 (Join-Path $desktop "package.json") | ConvertFrom-Json
    $version = [string]$package.version
    $artifactName = "CineSubStudio-$version-windows-x64-setup." + '${ext}'
    $env:CINESUB_BUILD_FLAVOR = $Flavor

    Push-Location $desktop
    try {
        $scriptName = if ($OnlyUnpacked) { "pack:win" } else { "dist:win" }
        if ($OnlyUnpacked) {
            Write-Info "Building unpacked Windows target (pack:win)..."
        } else {
            Write-Info "Building NSIS installer (dist:win)..."
        }
        & npm run $scriptName -- `
            "--config.directories.output=$resolvedOutput" `
            "--config.extraMetadata.cinesubBuildFlavor=$Flavor" `
            "--config.win.artifactName=$artifactName"
        if ($LASTEXITCODE -ne 0) {
            throw "electron-builder failed with exit code $LASTEXITCODE"
        }
        Write-Info "Build completed for flavor=$Flavor. Check $resolvedOutput"
    } finally {
        Pop-Location
    }

    $manifestScript = Join-Path $PSScriptRoot "generate_release_manifest.py"
    & (Join-Path $root "tools\python\python.exe") -B $manifestScript `
        --output-dir $resolvedOutput `
        --runtime-dir (Join-Path $PSScriptRoot "runtime") `
        --version $version `
        --flavor $Flavor
    if ($LASTEXITCODE -ne 0) {
        throw "Release manifest generation failed with exit code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:CINESUB_BUILD_FLAVOR -ErrorAction SilentlyContinue
    Pop-Location
}
