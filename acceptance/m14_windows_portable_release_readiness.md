# M14 Windows Portable Release Readiness

## Starting Point

- Starting point: M13 tip `c98d896 m13: improve local launcher readiness`.
- Source branch: `milestone13-local-launcher-readiness`.
- M14 branch: `milestone14-windows-portable-readiness`.

## Goal

M14 prepares a documented, repeatable, safe Windows portable staging workflow:

```text
source tree / tracked files
-> allowlisted portable staging layout
-> smoke validation
-> optional local zip artifact kept ignored/untracked
```

This is not an official release.

## Portable Layout Decision

The M14 staging helper creates a source-based portable readiness layout under a caller-provided staging directory such as:

```text
dist/CineSubStudio-portable/
```

The layout includes app source, Web UI, docs, safe config examples, startup files, runtime placeholder folders, and generated placement notes for FFmpeg and models.

## Packaging Helper Behavior

- New helper: `scripts/build_portable.ps1`.
- Dry-run command:

```powershell
.\scripts\build_portable.ps1 -DryRun -StagingDir dist\CineSubStudio-portable
```

- Staging command:

```powershell
.\scripts\build_portable.ps1 -StagingDir dist\CineSubStudio-portable
```

- Optional zip command:

```powershell
.\scripts\build_portable.ps1 -StagingDir dist\CineSubStudio-portable -Zip
```

The helper uses `git ls-files` as source inventory, then applies an allowlist before copying. It is not a whole-repository copy.

`-DryRun` is required to report included/excluded files without creating the staging directory, writing placeholder files, or generating a zip.

## Included Files

The allowlist includes:

- startup files: `start_web.ps1`, `start_app.py`
- install/runtime metadata: `install.ps1`, `requirements.txt`, `pyproject.toml`
- user README: `README.md`
- application source: `src/`
- single-file Web UI: `web/`
- documentation: `docs/`
- safe config examples: `config/*.example`, `config/*.sample`

## Excluded Files

The helper excludes:

- `.git/`, `.venv/`, `.cache/`, `.tmp/`
- `tests/`, `acceptance/`, `audit/`, `reports/`
- `input/`, `output/`, `work/`, `uploads/`, `archive/`, `failed/`, `logs/`
- `models/*`
- `tools/python/`, `tools/wheelhouse/`, `tools/cuda/`, `tools/ffmpeg/`
- `config/providers.local.json`
- `config/language_profiles.local.json`
- `.env` and `.env.*`
- token/API key/secret/password-looking filenames
- media, subtitle, zip, log, temp, runtime state, and quality report artifacts
- `project_evaluation_report.md`

These exclusions apply even if a local secret file is accidentally tracked.

## Secret Handling

Provider API keys and local Language Profile overrides remain runtime config only. They are not copied into staging.

The helper explicitly blocks local config and secret-looking filenames. It does not print API key values or Provider config contents.

## FFmpeg And Model Policy

FFmpeg binaries belong under:

```text
tools/ffmpeg/bin/
```

M14 does not commit FFmpeg binaries and does not download FFmpeg.

Models belong under `models/` or the supported project-local Hugging Face cache. M14 does not commit model files, download models, or add a model hub.

The staging helper dynamically writes placeholder notes inside the staging folder:

- `tools/ffmpeg/README_PLACE_FFMPEG_HERE.txt`
- `models/README_PLACE_MODELS_HERE.txt`

These placeholder files are not committed into the source tree.

## Runtime, Output, And Logs

Runtime folders are created as empty staging placeholders:

- `runtime/`
- `output/`
- `logs/`
- `work/`
- `uploads/`

Generated runtime files remain untracked and ignored.

## Older Release Builder

`scripts/build_portable_release.py` is preserved unchanged. It targets an older portable-runtime release-candidate shape that can copy a portable Python runtime.

`scripts/build_portable.ps1` is the M14 Windows staging helper. The two scripts have different goals, and M14 does not replace the older builder.

## Smoke Validation Commands

Planned validation:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_windows_portable_release_readiness.py -q
.\.venv\Scripts\python.exe -B -m pytest tests\test_local_launcher_readiness.py tests\test_web_settings_center.py tests\test_web_runtime_diagnostics_ui.py tests\test_web_queue_history_progress_ui.py tests\test_web_ui_productization.py -q
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
.\scripts\build_portable.ps1 -DryRun -StagingDir dist\CineSubStudio-portable
git diff --check
```

Results:

- `tests/test_windows_portable_release_readiness.py`: passed, `14 passed`.
- Related launcher/settings/runtime/UI regression tests: passed, `56 passed`.
- Import smoke: passed, `imports ok`.
- Launcher smoke: passed. No interactive prompt, no browser opened, no model download, FFmpeg status reported.
- Packaging dry-run: passed. It printed included/excluded manifests and did not create staging or zip artifacts.
- Full test suite: passed, `315 passed` with the existing `PytestUnhandledThreadExceptionWarning` from background `job_api.run_job` cleanup behavior.
- `git diff --check`: passed.

## Generated Artifact Status

- Local staging generated: yes.
- Path: `dist/CineSubStudio-portable/`.
- Local zip generated: no.
- `dist/CineSubStudio-portable.zip` was not created.
- The generated staging folder is under ignored `dist/` and did not appear in normal `git status --short --untracked-files=all`.
- No committed release zip.
- No official release artifact.

## Compatibility

M13 launcher behavior remains intact: `start_web.ps1` still supports `-NoBrowser`, `-Smoke`, `-NonInteractive`, and `-Port`.

M12 Provider/Profile ownership remains intact: Provider owns API Base/API Key/LLM model; Language Profile owns language, ASR defaults, quality thresholds, style, glossary, and subtitle preferences.

M11 runtime diagnostics fields remain intact, including `ffmpeg_source`, `diagnostic_summary`, `diagnostic_items`, `diagnostic_items[].status`, and `diagnostic_items[].blocking`.

## No Offline Standalone Validation

No offline standalone validation was performed in M14 because Python and `.venv` are not bundled by the M14 staging workflow.

## Explicit Non-Goals

- No pipeline rewrite.
- No database.
- No Electron/Tauri.
- No Windows installer.
- No auto-update.
- No code signing.
- No committed release zip.
- No committed FFmpeg binaries.
- No committed model files.
- No model hub or auto-download.
- No Chinese dubbing/TTS.
- No audio mixing, muxing, or lip-sync.
- No official release artifact.
- No M15 work.
