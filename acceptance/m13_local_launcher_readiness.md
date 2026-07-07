# M13 Local Launcher Readiness Acceptance

## Starting Point

- Started from M12 tip: `3c4ddcf m12: add provider and profile settings center`.
- Branch: `milestone13-local-launcher-readiness`.

## Goal

M13 improves desktopization readiness without implementing a desktop shell. The target startup flow is:

```text
run launcher -> runtime/startup checks -> local Web server -> browser or printed URL -> clear troubleshooting guidance
```

## Startup Changes

- `start_web.ps1` now exposes launcher parameters:
  - `-NoBrowser`
  - `-Smoke`
  - `-NonInteractive`
  - `-Port`
- `start_app.py` now has explicit argument parsing and startup phases.
- The launcher prints:
  - project root
  - Python executable
  - local URL
  - server log path
  - runtime diagnostics guidance
- `-Smoke -NoBrowser -NonInteractive` performs a non-interactive readiness check and exits.
- Smoke mode does not open a browser, start media processing, run ASR, translate, load models, or download models.
- Browser opening is skipped with `-NoBrowser`.
- Port conflicts are reported before server launch when the port is occupied by something that is not responding as CineSub Studio.
- Non-default ports are passed to the existing Web server through `SUBTITLE_WEB_PORT`.

## FFmpeg Missing Behavior

- Missing FFmpeg is reported clearly but does not block Web UI startup.
- Actual media jobs still require FFmpeg and will fail if it remains unavailable.
- The launcher prints accepted FFmpeg environment variables:
  - `CINESUB_FFMPEG`
  - `FFMPEG_PATH`
- The launcher points to project-local FFmpeg under `tools/ffmpeg/bin/` and the optional helper `.\scripts\download_ffmpeg.ps1`.

## Documentation Changes

- Added `docs/desktopization_readiness.md`.
- Added README startup notes for `-NoBrowser`, `-Smoke`, and `-NonInteractive`.
- Updated `AGENTS.md` to document the new launcher options and smoke command.

## Desktop Shell Evaluation

- Browser launcher: recommended for now; lowest risk and reuses the existing Web app.
- Electron: familiar desktop feel, but larger package and Node/process lifecycle complexity.
- Tauri: smaller shell, but adds Rust/Tauri integration complexity before portable release needs are settled.

Recommendation: continue with browser launcher / local Web service. Defer Electron/Tauri.

## Why No Desktop Shell Or Installer

M13 is desktopization readiness, not packaging. No Electron, Tauri, Windows installer, release zip, code signing, auto-update, model hub, or official release artifact was added.

## M11 / M12 Compatibility

- Runtime diagnostics API fields remain intact:
  - `ffmpeg_source`
  - `diagnostic_summary`
  - `diagnostic_items`
  - `diagnostic_items[].status`
  - `diagnostic_items[].blocking`
- Provider/Profile ownership remains unchanged:
  - Provider owns API Base / API Key / LLM model.
  - Language Profile owns language, ASR defaults, quality thresholds, style, glossary, and subtitle preferences.

## Test Results

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_local_launcher_readiness.py -q
```

Result: passed, `6 passed`.

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_web_settings_center.py tests\test_web_runtime_diagnostics_ui.py tests\test_web_queue_history_progress_ui.py tests\test_web_ui_productization.py tests\test_provider_language_profile_asr_boundary.py -q
```

Result: passed, `56 passed`. The process printed a background job thread exception after completion, consistent with existing async job test cleanup behavior; pytest returned exit code `0`.

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

Result: passed, `imports ok`.

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
```

Result: passed. No interactive prompt, no browser open, no model download, clear FFmpeg status.

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests -q
```

Result: passed. Full suite completed with `301 passed` and one `PytestUnhandledThreadExceptionWarning` from an existing background `job_api.run_job` test thread after job state cleanup.

## Explicit Non-Goals

- No pipeline rewrite.
- No database.
- No new scheduler.
- No batch recovery semantic changes.
- No ASR precedence changes.
- No Provider/Profile ownership changes.
- No Electron implementation.
- No Tauri implementation.
- No Windows installer.
- No portable release zip.
- No model hub or automatic model download.
- No Chinese dubbing, TTS, voice cloning, audio mixing, muxing, or lip-sync.
- No release artifact change.
- No M14 work.
