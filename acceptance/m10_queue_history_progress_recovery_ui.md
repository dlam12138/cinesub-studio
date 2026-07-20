# M10 Queue / History / Progress / Failure Recovery UI — Acceptance

## Starting Point

Branch: `milestone9-web-ui-productization`
Tip: `6b37f85 m9: productize web subtitle workflow`

New branch: `milestone10-queue-history-progress-ui`

## M10 Goal

Make job execution easier to monitor, inspect, and recover from by adding a visible job queue / history area, progress display, failure summary, and safe retry to the Web UI.

## UI Changes

### Queue / Recent Jobs

- Added a **"最近任务"** panel in the **单文件字幕** tab, below the current job status log.
- Displays the latest jobs in a scrollable list, sorted by update time (newest first).
- Each item shows:
  - File name
  - Status badge (等待中 / 处理中 / 已完成 / 失败)
  - Current stage label (转写 / 翻译 / 质检 / 已完成 / 失败)
  - Progress percentage when available
  - Last update timestamp

### Status / Progress Display

- Jobs expose `status`, `stage`, `progress`, `status_label`, `stage_label` in the backend.
- Running jobs show a progress bar when `progress` is numeric.
- If the backend cannot provide granular progress, the UI shows the stage text only (no fake percentage).
- The transcribe subprocess does not emit granular progress, so progress is stage-based:
  - `transcribing` → 10–30%
  - `translating` → 60%
  - `quality_checking` → 90%
  - `completed` / `failed` → 100%

### Failure Summary

- Failed jobs expose a concise `error_summary` extracted from the last 20 log lines.
- Long errors are truncated to ≤ 200 characters in the backend summary.
- The UI renders the error summary inside an expandable `<details>` block (`.job-error-details`), not dumped directly into the main status text.

### Result Visibility

- Completed (`done`) jobs show download links for:
  - 原文 SRT
  - 双语 SRT
- Output paths are displayed as copyable text.

### Retry Behavior

- A **"重新提交"** button appears only for failed jobs with a complete original request/options snapshot.
- Retry creates a **new job** with the same options; the original failed job record is **not mutated**.
- Retry is disabled with a tooltip/explanation when:
  - The job is not `failed`
  - The input file no longer exists
  - The original `options` or `input` are missing

### Auto Refresh / Polling

- The job queue auto-refreshes every **2 seconds** when active jobs exist.
- Slows to **8 seconds** when no jobs are running or queued.
- A manual **刷新** button is also provided.

## Backend Changes

### New / Changed Endpoints

- `GET /api/jobs` — already existed; now returns enriched fields:
  - `stage`, `stage_label`, `progress`, `status_label`
  - `error_summary`, `can_retry`, `retry_reason`, `completed_at`
- `POST /api/jobs/<job_id>/retry` — **new** thin endpoint.
  - Validates the job is `failed` and `can_retry`.
  - Calls `job_api.retry_job()` to create a new job.
  - Returns `201` with the new job object.

### `src/web/job_api.py`

- `create_job()` now initializes `stage`, `progress`, `error_summary`, `completed_at`.
- `run_job()` updates `stage` and `progress` during execution:
  - Starts at `stage="transcribing", progress=10`
  - Infers later stages from log keywords (`_infer_stage_from_logs`).
  - Sets `stage="completed", progress=100` on success.
  - Sets `stage="failed", progress=100, error_summary=...` on failure.
- `list_jobs()` now returns UI-friendly JSON via `_normalize_job_for_ui()`.
- `retry_job()` safely creates a new job from the failed job’s saved `options`.
- `_options_to_form()` converts saved options back into a form dict for `create_job()`.
- `_can_retry_job()` enforces retry safety rules.
- `_compute_error_summary()` extracts a concise error from logs.

### `src/web/web_server.py`

- Added `retry_job` import.
- In `do_POST`, added routing for `/api/jobs/<job_id>/retry` before the fallback 404.

## How Existing State Files Are Reused

- M10 does **not** introduce any new persistent state format.
- Jobs continue to live in the in-memory `JOBS` dict (same as M9).
- No migration of existing state files is needed; pre-M10 jobs get sensible defaults:
  - `progress` falls back to `0` for queued, `10` for running, `100` for done/failed.
  - `stage` falls back to empty string.

## Why No Database Was Introduced

- All job state remains in the existing `JOBS` in-memory dictionary.
- The Web UI only needs visibility for the current process lifetime; no cross-restart history is required.
- The acceptance criteria explicitly forbid introducing a database.

## How Retry Avoids Mutating Completed Jobs

- `retry_job()` only operates on jobs with `status == "failed"`.
- It creates a **new** job via `create_job()` using the original options.
- The original job record is never modified; it stays `failed` with its original `completed_at`.
- The new job gets a new `id` and `status == "queued"` (or `"running"` immediately after thread start).

## Provider / Language Profile / ASR Boundary Protection

- Retry preserves the exact saved `options` from the original job.
- `options` include `provider_id`, `api_provider`, `api_base`, `llm_model`, etc.
- No Provider legacy ASR fields (`whisper_model`, `whisper_device`) are injected into the retry form.
- ASR values (`model`, `device`, `language`, `beam_size`, etc.) come from the saved job options, not from Provider configuration.
- `translate_enabled` and translation settings are preserved exactly.

## Test Commands and Results

### New M10 tests

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -m pytest tests\test_web_queue_history_progress_ui.py -q
```

Result: **18 passed**

Coverage:
1. ✅ Page includes queue/history section
2. ✅ Recent jobs endpoint returns UI-friendly fields
3. ✅ Jobs sorted newest-first by `updated_at`
4. ✅ Running job includes status/stage/progress
5. ✅ Missing numeric progress does not produce fake percentage (fallback 10 for running)
6. ✅ Completed job exposes output path
7. ✅ Failed job exposes readable error summary
8. ✅ Long errors summarized / expandable details in UI
9. ✅ Retry enabled only when original options are complete
10. ✅ Retry creates a new job, does not mutate original
11. ✅ Retry disabled with explanation when metadata missing
12. ✅ Provider legacy ASR fields do not influence retry
13. ✅ UI does not expose TTS/dubbing/voice-clone controls
14. ✅ No database introduced
15. ✅ M9 UI sections still present

### M9 regression tests

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -m pytest tests\test_web_ui_productization.py tests\test_provider_language_profile_asr_boundary.py tests\test_effective_translation_config.py tests\test_language_profile_glossary.py -q
```

Result: **29 passed**

### Full test suite

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -m pytest tests -q
```

Result: **159 passed**

### Import smoke

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

Result: `imports ok`

## Explicit Non-Goals

- ❌ No pipeline rewrite
- ❌ No database introduced
- ❌ No desktop packaging (Electron, Tauri, etc.)
- ❌ No model management UI
- ❌ No Provider / Language Profile settings center expansion
- ❌ No Chinese dubbing / TTS / voice cloning
- ❌ No audio mixing, muxing, or lip-sync
- ❌ No release artifact change
- ❌ No M11+ work started
