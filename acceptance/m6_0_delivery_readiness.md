# M6.0 Delivery Readiness Acceptance

## Scope

M6.0 focuses on Windows first-run readiness:

- clearer install/start guidance
- first-run diagnostics for Python, `.venv`, FFmpeg, Provider, and writable runtime directories
- short README quickstart
- release-readiness checklist
- Run History design only

M6.0 does not implement PyInstaller packaging, portable runtime switching, Docker/cloud deployment, mixed-language segmented ASR, subtitle encoding auto-detection, Provider schema migration, large UI rewrites, or broad refactors.

## First-Run Checklist

Use this checklist before handing the project to a Windows user:

- Project starts from a clean checkout or release folder.
- Project path with Chinese characters and spaces is tested.
- `.\install.ps1` creates or updates `.venv/` without changing system PATH or PowerShell profile.
- `.\start_web.ps1` starts through `.venv\Scripts\python.exe -B start_app.py`.
- Missing `.venv` message tells the user to run `.\install.ps1`.
- Startup failure message points to `logs/web_server.log`.
- Web binds only to `127.0.0.1`.
- Runtime diagnostics API returns stable fields:
  - `ffmpeg_source`
  - `diagnostic_summary`
  - `diagnostic_items`
  - `diagnostic_items[].status`
  - `diagnostic_items[].blocking`
- Runtime diagnostics checks `output/`, `work/`, and `logs/` writability with temporary probe files that are deleted immediately.
- FFmpeg is detected from `tools/ffmpeg/bin/` when bundled FFmpeg exists.
- Provider incomplete is shown as Web-startup non-blocking and translation-use blocking.
- API keys and raw Provider config are not printed in diagnostics, logs, README, or acceptance notes.

## User Flow Checklist

Validate the normal Web flow:

- Open Web home page.
- Configure Provider or confirm diagnostics clearly reports Provider as incomplete.
- Put a media sample into `input/`.
- Click `扫描 input`.
- Click `开始处理 input`.
- Check `任务状态`.
- Check `操作日志`.
- Run `异常复核`.
- Download available artifacts from task output actions:
  - source SRT
  - translated or bilingual SRT
  - quality report
  - review-needed SRT

If `batch_worker.py --review` returns code `1` with a valid review summary, Web should treat it as `issues_found`, not as a command crash.

## Validation Commands

Run from the project root:

```powershell
git status --short
git diff --check
.\.venv\Scripts\python.exe -m pytest
```

Baseline import check:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

Core self-tests:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\core\subtitle_translate.py --self-test
.\.venv\Scripts\python.exe -B src\core\quality_checker.py --self-test
```

Runtime and pipeline checks:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\tools\runtime_env.py diagnostics
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --scan
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --status
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --review
```

Web smoke:

```powershell
.\scripts\smoke_test.ps1
```

Mojibake guard:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_text_encoding_hygiene.py
```

## Manual E2E Evidence

When real sample media and Provider credentials are available, run:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\pipeline\e2e_runner.py --config tests\e2e_samples\samples.example.json
```

Expected evidence:

- `reports/e2e_sample_report.json`
- `reports/e2e_sample_report.md`
- generated artifacts under `output/`
- any manual review notes based on `tests/e2e_samples/manual_review.template.md`

Do not commit private media, API keys, full Provider configs, or large runtime outputs.

## Run History Future Design

Run History is design-only in M6.0. This milestone must not create `logs/run_history.jsonl`, add run-history APIs, or implement persistent Web action logging.

Future storage:

```text
logs/run_history.jsonl
```

Future event fields:

```text
timestamp
source
action
status
returncode
duration_ms
provider_id
language_profile_id
sanitized_command
stdout_tail
stderr_tail
artifact_summary
```

Safety boundaries:

- Do not store API keys, tokens, secrets, or raw Provider config.
- Do not store full stdout/stderr for long tasks.
- Store project-relative paths when possible.
- Treat Run History as diagnostic metadata, not as the canonical task state.
