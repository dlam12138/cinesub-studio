param(
    [string]$Python = "python",
    [string[]]$PythonArgs = @(),
    [switch]$Recreate,
    [string]$IndexUrl = "https://pypi.org/simple"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "Project: $ProjectRoot"

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

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg was not found in PATH. Install ffmpeg first, then rerun this script."
}

$env:PIP_CACHE_DIR = Join-Path $ProjectRoot ".cache\pip"
$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot ".cache\huggingface\hub"

New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR, $env:HF_HOME, $env:HF_HUB_CACHE, (Join-Path $ProjectRoot "models"), (Join-Path $ProjectRoot "output"), (Join-Path $ProjectRoot "work") | Out-Null

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
        Invoke-Checked -FilePath $Python -Arguments ($PythonArgs + @("-m", "pip", "--python", ".\.venv\Scripts\python.exe", "install", "pip", "-i", $IndexUrl, "--timeout", "100", "--retries", "10"))
    }
}

& ".\.venv\Scripts\python.exe" -m pip --version
if ($LASTEXITCODE -ne 0) {
    Invoke-Checked -FilePath $Python -Arguments ($PythonArgs + @("-m", "pip", "--python", ".\.venv\Scripts\python.exe", "install", "pip", "-i", $IndexUrl, "--timeout", "100", "--retries", "10"))
}

Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "pip", "install", "--upgrade", "pip", "-i", $IndexUrl, "--timeout", "100", "--retries", "10")
Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "pip", "install", "-r", "requirements.txt", "-i", $IndexUrl, "--timeout", "100", "--retries", "10")

Write-Host ""
Write-Host "Installed. Run example:"
Write-Host '.\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model small -Device cpu'
