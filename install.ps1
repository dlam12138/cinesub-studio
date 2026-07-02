<#
.SYNOPSIS
Create or update the project-local Python virtual environment for CineSub Studio.

.DESCRIPTION
This script installs dependencies into .venv under the project directory. It does
not modify system PATH, PowerShell profile, global Python, or global pip cache.

Use -Offline with a wheelhouse when installing without internet access. Use
-Recreate only when you intentionally want to rebuild the existing .venv.
#>
param(
    [string]$Python = "python",
    [string[]]$PythonArgs = @(),
    [switch]$Recreate,
    [switch]$Offline,
    [string]$Wheelhouse = "tools\wheelhouse",
    [string]$IndexUrl = "https://pypi.org/simple"
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

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "Project: $ProjectRoot"
Write-Host "Install mode: $(if ($Offline) { 'offline wheelhouse' } else { 'online pip index' })"
Write-Host "Virtual environment: .venv"
Write-Host "No system PATH or PowerShell profile changes will be made."
if ($Recreate) {
    Write-Warning "Recreate requested: existing .venv will be removed after a project-path safety check."
} else {
    Write-Host "Existing .venv will be reused if present. Use -Recreate only when rebuilding is intentional."
}

$PortablePython = Join-Path $ProjectRoot "tools\python\python.exe"
if ($Python -eq "python" -and $PythonArgs.Count -eq 0 -and (Test-Path $PortablePython)) {
    $Python = $PortablePython
    Write-Host "Using bundled Python: $Python"
} else {
    Write-Host "Python launcher: $Python $($PythonArgs -join ' ')"
    Write-Host "Portable Python support is a future delivery option; start_web.ps1 will still use .venv."
}

$WheelhousePath = Join-Path $ProjectRoot $Wheelhouse
if ($Offline -and -not (Test-Path $WheelhousePath)) {
    throw "Offline install requested but wheelhouse was not found: $WheelhousePath"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

$versionOutput = & $Python @PythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
if ($LASTEXITCODE -ne 0) {
    throw "Could not run Python command: $Python $($PythonArgs -join ' ')"
}
Write-Host "Python: $versionOutput"

$majorMinor = $versionOutput.Split(".")[0..1] -join "."
if ([version]$majorMinor -lt [version]"3.9" -or [version]$majorMinor -gt [version]"3.12") {
    Write-Warning "faster-whisper is usually safest on Python 3.9-3.12. Current Python is $versionOutput."
    Write-Warning 'If installation fails, install Python 3.12 and run: .\install.ps1 -Python py -PythonArgs "-3.12"'
}

Write-Host "FFmpeg runtime check is handled by Python ffmpeg_locator.py."
Write-Host "To install the bundled Windows FFmpeg, run: .\scripts\download_ffmpeg.ps1"
if ($Offline) {
    Write-Host "Offline mode: pip will only use wheelhouse: $WheelhousePath"
} else {
    Write-Host "Online mode: pip index is $IndexUrl"
}

$env:PIP_CACHE_DIR = Join-Path $ProjectRoot ".cache\pip"
$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot ".cache\huggingface\hub"

New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR, $env:HF_HOME, $env:HF_HUB_CACHE, (Join-Path $ProjectRoot "models"), (Join-Path $ProjectRoot "output"), (Join-Path $ProjectRoot "work"), (Join-Path $ProjectRoot "logs") | Out-Null

if ($Recreate -and (Test-Path ".venv")) {
    $venvPath = Resolve-Path -LiteralPath ".venv"
    if (-not $venvPath.Path.StartsWith($ProjectRoot)) {
        throw "Refusing to remove venv outside project: $($venvPath.Path)"
    }
    Remove-Item -LiteralPath $venvPath.Path -Recurse -Force
}

if (-not (Test-Path ".venv")) {
    & $Python @PythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Standard venv creation failed. Retrying with --without-pip, then injecting pip."
        if (Test-Path ".venv") {
            $venvPath = Resolve-Path -LiteralPath ".venv"
            if (-not $venvPath.Path.StartsWith($ProjectRoot)) {
                throw "Refusing to remove venv outside project: $($venvPath.Path)"
            }
            Remove-Item -LiteralPath $venvPath.Path -Recurse -Force
        }

        Invoke-Checked -FilePath $Python -Arguments ($PythonArgs + @("-m", "venv", "--without-pip", ".venv"))
        $pipBootstrapArgs = @("-m", "pip", "--python", ".\.venv\Scripts\python.exe", "install", "pip")
        if ($Offline) {
            $pipBootstrapArgs += @("--no-index", "--find-links", $WheelhousePath)
        } else {
            $pipBootstrapArgs += @("-i", $IndexUrl, "--timeout", "100", "--retries", "10")
        }
        Invoke-Checked -FilePath $Python -Arguments ($PythonArgs + $pipBootstrapArgs)
    }
}

& ".\.venv\Scripts\python.exe" -m pip --version
if ($LASTEXITCODE -ne 0) {
    $pipBootstrapArgs = @("-m", "pip", "--python", ".\.venv\Scripts\python.exe", "install", "pip")
    if ($Offline) {
        $pipBootstrapArgs += @("--no-index", "--find-links", $WheelhousePath)
    } else {
        $pipBootstrapArgs += @("-i", $IndexUrl, "--timeout", "100", "--retries", "10")
    }
    Invoke-Checked -FilePath $Python -Arguments ($PythonArgs + $pipBootstrapArgs)
}

if ($Offline) {
    Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "pip", "install", "--no-index", "--find-links", $WheelhousePath, "-r", "requirements.txt")
} else {
    Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "pip", "install", "--upgrade", "pip", "-i", $IndexUrl, "--timeout", "100", "--retries", "10")
    Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "pip", "install", "-r", "requirements.txt", "-i", $IndexUrl, "--timeout", "100", "--retries", "10")
}

Write-Host ""
Write-Host "Installed."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Start the Web UI: .\start_web.ps1"
Write-Host "  2. Open: http://127.0.0.1:7860"
Write-Host "  3. Check Web diagnostics at the top of the page."
Write-Host "  4. If FFmpeg is missing, run: .\scripts\download_ffmpeg.ps1"
Write-Host ""
Write-Host "Single-file CLI example:"
Write-Host '  .\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model small -Device auto'
