param(
    [string]$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    [switch]$Force
)

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

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARN: $Message" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Get-ProjectRoot {
    # scripts/download_ffmpeg.ps1 -> project root is parent of scripts/
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

$ProjectRoot = Get-ProjectRoot
$TmpRoot = Join-Path $ProjectRoot ".tmp"
$TmpFfmpeg = Join-Path $TmpRoot "ffmpeg-download"
$ZipPath = Join-Path $TmpFfmpeg "ffmpeg-release-essentials.zip"
$ExtractDir = Join-Path $TmpFfmpeg "extract"
$InstallBin = Join-Path $ProjectRoot "tools\ffmpeg\bin"

$FfmpegExe = Join-Path $InstallBin "ffmpeg.exe"
$FfprobeExe = Join-Path $InstallBin "ffprobe.exe"
$FfplayExe = Join-Path $InstallBin "ffplay.exe"

Write-Step "CineSub Studio FFmpeg downloader"
Write-Host "Project root: $ProjectRoot"
Write-Host "Install bin : $InstallBin"
Write-Host "Download URL: $Url"

if ((Test-Path $FfmpegExe) -and (Test-Path $FfprobeExe) -and (-not $Force)) {
    Write-Ok "Built-in FFmpeg already exists."
    Write-Host "ffmpeg : $FfmpegExe"
    Write-Host "ffprobe: $FfprobeExe"
    Write-Host ""
    Write-Host "Use -Force to re-download."
    exit 0
}

Write-Step "Preparing directories"
New-Item -ItemType Directory -Force -Path $TmpFfmpeg | Out-Null
New-Item -ItemType Directory -Force -Path $InstallBin | Out-Null

if (Test-Path $ExtractDir) {
    Remove-Item -LiteralPath $ExtractDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $ExtractDir | Out-Null

Write-Step "Downloading FFmpeg"
try {
    # Invoke-WebRequest works in Windows PowerShell and PowerShell Core.
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing
} catch {
    Fail "Download failed: $($_.Exception.Message)"
}

if (-not (Test-Path $ZipPath)) {
    Fail "Download did not create zip file: $ZipPath"
}

$ZipSize = (Get-Item $ZipPath).Length
if ($ZipSize -lt 10000000) {
    Fail "Downloaded file looks too small: $ZipSize bytes"
}
Write-Ok "Downloaded zip: $ZipPath ($ZipSize bytes)"

Write-Step "Extracting FFmpeg"
try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractDir -Force
} catch {
    Fail "Extract failed: $($_.Exception.Message)"
}

$FoundFfmpeg = Get-ChildItem -LiteralPath $ExtractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$FoundFfprobe = Get-ChildItem -LiteralPath $ExtractDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
$FoundFfplay = Get-ChildItem -LiteralPath $ExtractDir -Recurse -Filter "ffplay.exe" | Select-Object -First 1

if (-not $FoundFfmpeg) {
    Fail "ffmpeg.exe not found after extraction."
}
if (-not $FoundFfprobe) {
    Fail "ffprobe.exe not found after extraction."
}

Write-Ok "Found ffmpeg : $($FoundFfmpeg.FullName)"
Write-Ok "Found ffprobe: $($FoundFfprobe.FullName)"

Write-Step "Installing to project tools directory"
Copy-Item -LiteralPath $FoundFfmpeg.FullName -Destination $FfmpegExe -Force
Copy-Item -LiteralPath $FoundFfprobe.FullName -Destination $FfprobeExe -Force

if ($FoundFfplay) {
    Copy-Item -LiteralPath $FoundFfplay.FullName -Destination $FfplayExe -Force
    Write-Ok "Installed ffplay : $FfplayExe"
} else {
    Write-Warn "ffplay.exe not found. This is not required for CineSub pipeline."
}

Write-Ok "Installed ffmpeg : $FfmpegExe"
Write-Ok "Installed ffprobe: $FfprobeExe"

Write-Step "Verifying binaries"
try {
    $psiFfmpeg = New-Object System.Diagnostics.ProcessStartInfo
    $psiFfmpeg.FileName = $FfmpegExe
    $psiFfmpeg.Arguments = "-version"
    $psiFfmpeg.RedirectStandardOutput = $true
    $psiFfmpeg.UseShellExecute = $false
    $procFfmpeg = [System.Diagnostics.Process]::Start($psiFfmpeg)
    $ffmpegVersion = $procFfmpeg.StandardOutput.ReadLine()
    $procFfmpeg.WaitForExit()

    $psiFfprobe = New-Object System.Diagnostics.ProcessStartInfo
    $psiFfprobe.FileName = $FfprobeExe
    $psiFfprobe.Arguments = "-version"
    $psiFfprobe.RedirectStandardOutput = $true
    $psiFfprobe.UseShellExecute = $false
    $procFfprobe = [System.Diagnostics.Process]::Start($psiFfprobe)
    $ffprobeVersion = $procFfprobe.StandardOutput.ReadLine()
    $procFfprobe.WaitForExit()
} catch {
    Fail "Verification failed: $($_.Exception.Message)"
}

Write-Ok "$ffmpegVersion"
Write-Ok "$ffprobeVersion"

Write-Step "Checking project locator if available"
$LocatorPath = Join-Path $ProjectRoot "src\tools\ffmpeg_locator.py"
if (Test-Path $LocatorPath) {
    try {
        Push-Location $ProjectRoot
        $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path $python)) {
            $python = "python"
        }

        $code = @"
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "src" / "tools"))

import ffmpeg_locator

path = ffmpeg_locator.find_ffmpeg()
print(path)
"@

        $located = & $python -B -c $code
        Write-Ok "ffmpeg_locator.find_ffmpeg() => $located"
    } catch {
        Write-Warn "Locator check failed: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
} else {
    Write-Warn "src/tools/ffmpeg_locator.py not found. Skipping locator check."
}

Write-Step "Done"
Write-Host "Built-in FFmpeg is installed at:"
Write-Host "  $FfmpegExe"
Write-Host ""
Write-Host "This script does not modify system PATH."
Write-Host "Do not commit tools/ffmpeg/bin/*.exe to Git."
