$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SmokeDir = Join-Path $ProjectRoot ".tmp\smoke"
$SmokePort = 7861

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\install.ps1 first."
}

New-Item -ItemType Directory -Force -Path $SmokeDir | Out-Null
Set-Location $ProjectRoot

$SrcPath = "src\core;src\pipeline;src\config;src\web;src\tools"
$env:PYTHONPATH = $SrcPath
$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot ".cache\huggingface\hub"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:SUBTITLE_WEB_PORT = [string]$SmokePort

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Script
    )
    Write-Host "`n== $Name =="
    & $Script
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

function Invoke-ReviewStep {
    Write-Host "`n== pipeline review =="
    $output = & $Python -B "src\pipeline\batch_worker.py" --review 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String)
    Write-Host $text
    $hasValidSummary = $text.Contains("Review summary") -and (
        $text.Contains("Reports:") -or $text.Contains("Review subtitles:")
    )
    if ($exitCode -eq 0) {
        return
    }
    if ($exitCode -eq 1 -and $hasValidSummary) {
        Write-Host "pipeline review found quality issues; treating returncode=1 as issues_found."
        return
    }
    throw "pipeline review failed with exit code $exitCode"
}

function Get-HttpStatusCode {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutMs = 5000
    )
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = "GET"
    $request.Timeout = $TimeoutMs
    $request.ReadWriteTimeout = $TimeoutMs
    $response = $null
    try {
        $response = [System.Net.HttpWebResponse]$request.GetResponse()
        return [int]$response.StatusCode
    } finally {
        if ($response) {
            $response.Close()
        }
    }
}

Invoke-Step "syntax" {
    & $Python -B -c "from pathlib import Path; files=['src/core/transcribe.py','src/core/subtitle_translate.py','src/core/quality_checker.py','src/pipeline/batch_worker.py','src/pipeline/output_paths.py','src/web/web_server.py','src/web/job_api.py','src/web/pipeline_api.py','src/web/runtime_api.py','src/tools/runtime_env.py']; [compile(Path(p).read_text(encoding='utf-8-sig'), p, 'exec') for p in files]; print('syntax ok')"
}

Invoke-Step "imports" {
    & $Python -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, output_paths, web_server, job_api, download_model_file, runtime_env, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
}

Invoke-Step "subtitle translate self-test" {
    & $Python -B "src\core\subtitle_translate.py" --self-test
}

Invoke-Step "quality checker self-test" {
    & $Python -B "src\core\quality_checker.py" --self-test
}

Invoke-Step "runtime diagnostics" {
    & $Python -B "src\tools\runtime_env.py" diagnostics
}

Invoke-Step "pipeline scan" {
    & $Python -B "src\pipeline\batch_worker.py" --scan
}

Invoke-Step "pipeline status" {
    & $Python -B "src\pipeline\batch_worker.py" --status
}

Invoke-ReviewStep

Write-Host "`n== web smoke =="
$OutLog = Join-Path $SmokeDir "web.out.log"
$ErrLog = Join-Path $SmokeDir "web.err.log"
$stdout = [System.IO.StreamWriter]::new($OutLog, $false, [System.Text.UTF8Encoding]::new($false))
$stderr = [System.IO.StreamWriter]::new($ErrLog, $false, [System.Text.UTF8Encoding]::new($false))
$proc = $null
$stdoutEvent = $null
$stderrEvent = $null

try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $Python
    $psi.WorkingDirectory = $ProjectRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.Arguments = "-B -m src.web.web_server"
    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    $stdoutEvent = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
        if ($EventArgs.Data) { $Event.MessageData.WriteLine($EventArgs.Data); $Event.MessageData.Flush() }
    } -MessageData $stdout
    $stderrEvent = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data) { $Event.MessageData.WriteLine($EventArgs.Data); $Event.MessageData.Flush() }
    } -MessageData $stderr

    if (-not $proc.Start()) {
        throw "Could not start web smoke process."
    }

    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if ($proc.HasExited) {
            throw "Web process exited early with code $($proc.ExitCode). See $ErrLog"
        }
        try {
            $homeStatus = Get-HttpStatusCode -Uri "http://127.0.0.1:$SmokePort/" -TimeoutMs 2000
            if ($homeStatus -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # Keep polling until timeout.
        }
    }

    if (-not $ready) {
        throw "Web server did not become ready on port $SmokePort. See $OutLog and $ErrLog"
    }

    $homeStatus = Get-HttpStatusCode -Uri "http://127.0.0.1:$SmokePort/" -TimeoutMs 5000
    $diagStatus = Get-HttpStatusCode -Uri "http://127.0.0.1:$SmokePort/api/runtime/diagnostics" -TimeoutMs 10000
    Write-Host "home=$homeStatus diagnostics=$diagStatus"
} finally {
    if ($proc -and -not $proc.HasExited) {
        $proc.Kill()
        $proc.WaitForExit(5000) | Out-Null
    }
    if ($stdoutEvent) { Unregister-Event -SubscriptionId $stdoutEvent.Id -ErrorAction SilentlyContinue }
    if ($stderrEvent) { Unregister-Event -SubscriptionId $stderrEvent.Id -ErrorAction SilentlyContinue }
    $stdout.Dispose()
    $stderr.Dispose()
}

Write-Host "`nSmoke test completed."
