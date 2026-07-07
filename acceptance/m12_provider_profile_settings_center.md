# M12 Provider / Language Profile Settings Center Acceptance

## Starting Point

- Started from M11 tip: `d18a00f m11: add runtime diagnostics UI`.
- Working branch: `milestone12-provider-profile-settings-ui`.
- Existing audit zips, audit build folders, reports, logs, screenshots, and runtime outputs are intentionally left untracked.

## Goal

M12 productizes configuration management in the Web UI without changing pipeline behavior.

- Provider settings manage translation API configuration only.
- Language Profile settings manage language, ASR defaults, translation style, glossary, and subtitle preferences.
- Runtime diagnostics remains a diagnostics surface.
- Job and pipeline pages remain submission/tracking surfaces.

## UI Changes

- Added clearer settings-center copy to the `翻译接口` tab:
  - Provider is LLM/API-only.
  - ASR model/device/language settings belong to Language Profile.
  - API Key edit field remains blank on edit and explains that blank keeps the old key.
- Provider list now exposes user-facing setting columns for protocol, API address, model, masked key state, enabled state, notes, and actions.
- Added clearer settings-center copy to the `语言风格` tab:
  - Language Profile owns ASR defaults and translation preferences.
  - Explicit job ASR fields still override profile defaults.
- Language Profile list now summarizes language pair, ASR defaults, style/glossary status, builtin/local type, and actions.
- Added settings jump buttons from job/pipeline Provider/Profile selectors.
- Added non-blocking success/error notices for Provider/Profile save, delete, activate, and Provider test actions.
- Removed stale duplicate Provider connection-test JavaScript comments.
- Kept `web/index.html` as a single static file; no React/Vue/npm/CDN/build system was introduced.

## Backend Changes

- Added sanitized detail endpoints:
  - `GET /api/providers/<id>`
  - `GET /api/language-profiles/<id>`
- Existing route shape and store ID semantics are preserved.
- Provider UI-facing responses use `api_key_masked` and omit raw `api_key`.
- Provider test results are scrubbed at both store and route boundaries before reaching the browser.
- URL-encoded route IDs are decoded before passing to existing store helpers.
- Existing Provider create/update/delete/activate/test endpoints are preserved.
- Existing Language Profile create/update/delete/activate endpoints are preserved.

## Boundary Guarantees

- Provider remains LLM/API-only:
  - `id`
  - `name`
  - `template_id`
  - `protocol`
  - `api_base`
  - `api_key`
  - `translation_model`
  - `enabled`
  - `notes`
- Provider does not own ASR fields.
- Provider does not own temperature; temperature remains a per-job translation option.
- Editing Provider with a blank API Key keeps the existing key.
- No clear-key action was added in M12.
- Language Profile owns ASR defaults:
  - `whisper_model`
  - `whisper_device`
  - `compute_type`
  - `language`
  - `vad_filter`
  - `beam_size`
  - `condition_on_previous_text`
- Built-in/local profile merge semantics are unchanged:
  - Built-in profiles remain available.
  - Deleting a built-in ID removes only a local override.
  - Local profile writes continue to use the existing atomic write path.
- ASR precedence is unchanged:
  - CLI/Web explicit request
  - Language Profile
  - defaults

## Secret Handling

- Full API keys are never returned by UI-facing Provider list/detail/active responses.
- Provider connection-test failures are scrubbed before being returned to the browser.
- Tests use temporary Provider/Profile config paths and do not touch real local config files.
- Acceptance evidence does not include real API keys.

## Validation

- `.\.venv\Scripts\python.exe -B -m pytest tests\test_web_settings_center.py -q`
  - Passed: `7 passed`.
- `.\.venv\Scripts\python.exe -B -m pytest tests\test_web_runtime_diagnostics_ui.py tests\test_web_queue_history_progress_ui.py tests\test_web_ui_productization.py tests\test_provider_language_profile_asr_boundary.py tests\test_effective_translation_config.py tests\test_language_profile_glossary.py -q`
  - Passed: `54 passed`.
  - Existing background job thread warnings were observed from prior job test behavior.
- Import smoke:
  - Passed: `imports ok`.
- `.\.venv\Scripts\python.exe -B -m pytest tests -q`
  - Passed: `295 passed`.
  - Existing background job thread warning observed.
- One-shot local Web check using `ThreadingHTTPServer`:
  - `GET /` returned `200`.
  - `GET /api/runtime/diagnostics` returned `200`.

Final `git diff --check` and staged diff checks are recorded before commit.

## Explicit Non-Goals

- No pipeline rewrite.
- No database or new config system.
- No new job scheduler.
- No change to job submission semantics.
- No change to runtime diagnostics semantics.
- No change to `transcribe_to_srt()` core behavior.
- No desktop packaging, installer, Electron, Tauri, or portable release work.
- No model hub or automatic model download work.
- No Chinese dubbing, TTS, voice cloning, audio mixing, muxing, or lip-sync.
- No release artifact change.
- No M13 work.
