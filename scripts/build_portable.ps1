param(
    [Parameter(Mandatory = $true)]
    [string]$StagingDir,
    [switch]$Zip,
    [switch]$DryRun,
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

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptDir "..")).Path
$VersionPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VersionPython)) { throw "Missing project Python for version validation: $VersionPython" }
& $VersionPython -B (Join-Path $ProjectRoot "src\tools\versioning.py") check
if ($LASTEXITCODE -ne 0) { throw "Release version consumers do not match VERSION." }

function Convert-ToRepoRelative {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
    $rootWithSlash = $ProjectRoot.TrimEnd('\') + '\'
    if ($full.StartsWith($rootWithSlash, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $full.Substring($rootWithSlash.Length).Replace('\', '/')
    }
    return $full.Replace('\', '/')
}

function Resolve-StagingPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
}

function Test-UnderPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Base
    )
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
    $baseFull = [System.IO.Path]::GetFullPath($Base).TrimEnd('\') + '\'
    return $full.StartsWith($baseFull, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-AllowedFile {
    param([Parameter(Mandatory = $true)][string]$Relative)

    $normalized = $Relative.Replace('\', '/')
    $name = [System.IO.Path]::GetFileName($normalized)
    $lower = $normalized.ToLowerInvariant()
    $lowerName = $name.ToLowerInvariant()

    $hardBlockedExact = @(
        "config/providers.local.json",
        "config/language_profiles.local.json",
        "project_evaluation_report.md"
    )
    if ($hardBlockedExact -contains $lower) { return $false }
    if ($lowerName -eq ".env" -or $lowerName.StartsWith(".env.")) { return $false }
    if ($lowerName -match "(token|secret|api[_-]?key|password)") { return $false }
    if ($lower -match "(^|/)(\.git|\.venv|\.cache|\.tmp|audit|tests|acceptance|logs|output|work|uploads|input|archive|failed|models|reports)(/|$)") { return $false }
    if ($lower -match "(^|/)tools/(python|wheelhouse|cuda|ffmpeg)(/|$)") { return $false }
    if ($lower -match "\.(zip|7z|rar|log|tmp|bak|srt|mp4|mkv|mov|avi|wav|mp3|flac|aac|pyc|pyo|pyd)$") { return $false }
    if ($lower -match "\.(state|lang|quality_report)\.json$") { return $false }
    if ($lower.EndsWith("review_needed.srt")) { return $false }

    $allowedExact = @(
        "README.md",
        "requirements.txt",
        "pyproject.toml",
        "VERSION",
        "start_app.py",
        "start_web.ps1",
        "install.ps1"
    )
    if ($allowedExact -contains $normalized) { return $true }

    if ($normalized.StartsWith("src/")) { return $true }
    if ($normalized.StartsWith("web/")) { return $true }
    if ($normalized.StartsWith("docs/")) { return $true }
    if ($normalized.StartsWith("config/") -and ($lowerName.EndsWith(".example") -or $lowerName.EndsWith(".sample") -or $lowerName -eq "readme.md")) {
        return $true
    }

    return $false
}

function Get-TrackedFiles {
    Push-Location $ProjectRoot
    try {
        $output = & git ls-files -z
        if ($LASTEXITCODE -ne 0) {
            throw "git ls-files failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
    return @($output -split "`0" | Where-Object { $_ })
}

function Write-PlaceholderFiles {
    param([Parameter(Mandatory = $true)][string]$Root)

    $quickstart = @"
# CineSub Studio Portable Quickstart

This folder was prepared by the M14 Windows portable staging helper.
It is a release-readiness artifact, not an official release.

1. Make sure the required Python environment is available. M14 does not bundle Python.
2. Place FFmpeg binaries under tools/ffmpeg/bin/ or set CINESUB_FFMPEG / FFMPEG_PATH.
3. Place ASR models under models/ or the supported local Hugging Face cache.
4. Start the app with .\start_web.ps1.
5. Open http://127.0.0.1:7860/ if the browser does not open automatically.
6. Use the runtime diagnostics tab to check Python, FFmpeg, CUDA, wheelhouse, and model readiness.
7. Configure the translation Provider and Language Profile before translation jobs.

Smoke validation:

.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
"@

    $ffmpegReadme = @"
# FFmpeg Placement

Place FFmpeg binaries here:

tools/ffmpeg/bin/ffmpeg.exe
tools/ffmpeg/bin/ffprobe.exe

FFmpeg binaries are not committed and are not generated by M14.
You may also point to an existing FFmpeg with CINESUB_FFMPEG or FFMPEG_PATH.
"@

    $modelsReadme = @"
# Model Placement

Place local ASR model files under models/ or the supported project-local Hugging Face cache.
M14 does not download models and does not create a model hub.
Smoke validation must not require real models.
"@

    Set-Content -LiteralPath (Join-Path $Root "README_QUICKSTART.md") -Value $quickstart -Encoding utf8
    Set-Content -LiteralPath (Join-Path $Root "tools\ffmpeg\README_PLACE_FFMPEG_HERE.txt") -Value $ffmpegReadme -Encoding utf8
    Set-Content -LiteralPath (Join-Path $Root "models\README_PLACE_MODELS_HERE.txt") -Value $modelsReadme -Encoding utf8
}

$ResolvedStaging = Resolve-StagingPath -Path $StagingDir
$DistRoot = Join-Path $ProjectRoot "dist"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$RootWithSlash = $ProjectRoot.TrimEnd('\') + '\'

if ($ResolvedStaging.TrimEnd('\').Equals($ProjectRoot.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use project root as staging directory."
}
if (-not $ResolvedStaging.StartsWith($RootWithSlash, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "StagingDir must be inside the project root: $ResolvedStaging"
}
if ($Zip -and -not ((Test-UnderPath -Path $ResolvedStaging -Base $DistRoot) -or (Test-UnderPath -Path $ResolvedStaging -Base $ReleaseRoot))) {
    throw "-Zip requires StagingDir under ignored dist/ or release/: $ResolvedStaging"
}

$tracked = Get-TrackedFiles
$included = New-Object System.Collections.Generic.List[string]
$excluded = New-Object System.Collections.Generic.List[string]
foreach ($file in $tracked) {
    $relative = $file.Replace('\', '/')
    if (Test-AllowedFile -Relative $relative) {
        $included.Add($relative)
    } else {
        $excluded.Add($relative)
    }
}

Write-Host "CineSub Studio M14 portable staging helper"
Write-Host "Project:    $ProjectRoot"
Write-Host "StagingDir: $ResolvedStaging"
if ($DryRun) {
    Write-Host "Mode:       dry-run"
} else {
    Write-Host "Mode:       build"
}
if ($DryRun) { Write-Host "DryRun:     no files or directories will be written" }
Write-Host "Zip:        $($Zip.IsPresent)"
Write-Host ""
Write-Host "Included tracked files: $($included.Count)"
foreach ($file in $included) { Write-Host "  + $file" }
Write-Host ""
Write-Host "Excluded tracked files: $($excluded.Count)"
foreach ($file in $excluded) { Write-Host "  - $file" }

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry-run complete. No staging directory or zip was generated."
    exit 0
}

if (Test-Path -LiteralPath $ResolvedStaging) {
    if (-not $Force) {
        throw "StagingDir already exists. Use -Force to replace it: $ResolvedStaging"
    }
    Remove-Item -LiteralPath $ResolvedStaging -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $ResolvedStaging | Out-Null
foreach ($dir in @("config", "tools\ffmpeg", "models", "runtime", "output", "logs", "work", "uploads")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ResolvedStaging $dir) | Out-Null
}

foreach ($file in $included) {
    $source = Join-Path $ProjectRoot $file
    $target = Join-Path $ResolvedStaging ($file.Replace('/', '\'))
    $targetDir = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
}

Write-PlaceholderFiles -Root $ResolvedStaging

if ($Zip) {
    $zipPath = "$ResolvedStaging.zip"
    if (Test-Path -LiteralPath $zipPath) {
        if (-not $Force) {
            throw "Zip already exists. Use -Force to replace it: $zipPath"
        }
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $ResolvedStaging -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host ""
    Write-Host "Zip generated: $zipPath"
    Write-Host "This is a local test artifact, not an official release."
}

Write-Host ""
Write-Host "Portable staging generated: $ResolvedStaging"
Write-Host "Generated artifacts must remain ignored/untracked."
