param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,

    [string]$Model = "small",
    [ValidateSet("cpu", "cuda", "auto")]
    [string]$Device = "auto",
    [string]$ComputeType = "",
    [string]$Language = "",
    [string]$OutputDir = "output",
    [string]$SubtitleFormats = "srt",
    [string]$AssStyleId = "clean-cn"
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

$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot ".cache\huggingface\hub"

$srcDirs = @("core", "pipeline", "config", "web", "tools" | ForEach-Object { Join-Path $ProjectRoot "src\$_" })
$env:PYTHONPATH = ($srcDirs -join ";")

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\install.ps1 first."
}

$argsList = @(
    "src/core/transcribe.py",
    $InputFile,
    "--model", $Model,
    "--device", $Device,
    "--output-dir", $OutputDir,
    "--model-dir", "models",
    "--work-dir", "work",
    "--subtitle-formats", $SubtitleFormats,
    "--ass-style-id", $AssStyleId
)

if ($ComputeType -ne "") {
    $argsList += @("--compute-type", $ComputeType)
}

if ($Language -ne "") {
    $argsList += @("--language", $Language)
}

& ".\.venv\Scripts\python.exe" @argsList
