# M11 Runtime Diagnostics UI Acceptance

## Starting Point

- Branch started from M10 tip: `292b0db m10: add queue history and recovery UI`.
- Working branch: `milestone11-runtime-model-ffmpeg-ui`.
- Existing audit zips, audit build folders, reports, logs, screenshots, and runtime outputs are intentionally left untracked.

## Goal

M11 adds a Web-facing Runtime Diagnostics UI. It is not a full Model Management Center.

The UI answers:

- Is the local Python/app runtime visible?
- Is FFmpeg detected, and where will it be loaded from?
- Are model/cache directories visible and writable?
- Are important runtime directories available or creatable by the normal runtime?
- Is CUDA/device capability known from existing cheap diagnostics?
- What should the user do when something is missing?

## UI Changes

- Added a dedicated `运行环境` tab.
- Kept a compact runtime readiness summary on the pipeline page so M10 queue/history/retry remains intact.
- Added runtime sections for:
  - `环境检查`
  - `FFmpeg 状态`
  - `模型状态`
  - `运行目录`
  - `设备信息`
  - `Python / 应用运行时`
- Added a manual `刷新诊断` button.
- Warning/error guidance uses practical local setup language, including `CINESUB_FFMPEG` / `FFMPEG_PATH`.

## Backend Changes

- Reused the existing read-only `GET /api/runtime/diagnostics` path.
- Reused `runtime_api.py` and `runtime_env.py`; no parallel diagnostics service was added.
- Kept stable diagnostics fields backward compatible:
  - `ffmpeg_source`
  - `diagnostic_summary`
  - `diagnostic_items`
  - `diagnostic_items[].status`
  - `diagnostic_items[].blocking`
- Added optional `details` objects for UI-friendly display.
- Added FFmpeg version probing with a short timeout. Probe failure is contained in the FFmpeg diagnostic item and does not make the endpoint return 500.
- Added Python/platform/cwd/app-root/runtime-root fields.
- Added structured runtime directory diagnostics with `exists` and `writable`.
- Added model/cache status details without model downloads.

## Runtime Behavior

- `transcribe_to_srt()` behavior was not changed.
- Pipeline scan/run/retry/recovery behavior was not changed.
- No database or scheduler was introduced.
- No Provider/Profile settings center work was started.
- No model hub, model store, model installer, or automatic model download was added.
- The UI does not call `download_model_file.py`.
- Directory diagnostics do not create new persistent runtime directories just to check them.
- CUDA/device information uses the existing runtime diagnostics path and does not add heavy GPU imports.

## Validation

- `.\.venv\Scripts\python.exe -B -m pytest tests\test_web_runtime_diagnostics_ui.py -q`
  - Passed: `7 passed`.
- `.\.venv\Scripts\python.exe -B -m pytest tests\test_web_queue_history_progress_ui.py tests\test_web_ui_productization.py tests\test_provider_language_profile_asr_boundary.py -q`
  - Passed: `42 passed`.
  - Existing background job thread warnings were observed from prior job test behavior.
- Import smoke:
  - Passed: `imports ok`.
- `.\.venv\Scripts\python.exe -B -m pytest tests -q`
  - Passed: full suite green.
  - Existing background job thread warning observed.
- Web check:
  - `.\start_web.ps1` was attempted, but the current machine has no detected FFmpeg and the launcher opens a confirmation dialog before starting the server. In hidden automation this cannot be answered.
  - Backend Web server was then started directly with the project `.venv` and existing PYTHONPATH.
  - `GET http://127.0.0.1:7860/` returned `200`.
  - `GET http://127.0.0.1:7860/api/runtime/diagnostics` returned `200`.

## Explicit Non-Goals

- No pipeline rewrite.
- No database.
- No desktop packaging, installer, Electron, or Tauri work.
- No Provider/Profile settings center expansion.
- No Chinese dubbing, TTS, voice cloning, audio mixing, muxing, or lip-sync.
- No model hub or automatic model installation.
- No release artifact change.
- No M12 work.
