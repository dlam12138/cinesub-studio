param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,

    [string]$Model = "small",
    [ValidateSet("cpu", "cuda", "auto")]
    [string]$Device = "cpu",
    [string]$ComputeType = "",
    [string]$Language = "",
    [string]$OutputDir = "output"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot ".cache\huggingface\hub"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\install.ps1 first."
}

$argsList = @(
    "transcribe.py",
    $InputFile,
    "--model", $Model,
    "--device", $Device,
    "--output-dir", $OutputDir,
    "--model-dir", "models",
    "--work-dir", "work"
)

if ($ComputeType -ne "") {
    $argsList += @("--compute-type", $ComputeType)
}

if ($Language -ne "") {
    $argsList += @("--language", $Language)
}

& ".\.venv\Scripts\python.exe" @argsList

