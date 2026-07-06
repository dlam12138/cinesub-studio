# M8.9 Provider / Language Profile ASR Boundary

## Problem

M8.9 addresses a legacy-field ownership risk: old local Provider configs may retain
ASR fields such as `whisper_model` and `whisper_device`. If those fields coexist
with Language Profile ASR settings, ownership becomes unclear even when the
current UI and example Provider config no longer expose ASR fields.

## Final Rule

ASR settings are resolved from:

```text
CLI explicit ASR args > Language Profile ASR settings > built-in defaults
```

Provider config is LLM-only. Legacy Provider-side ASR fields are tolerated for
backward compatibility, ignored for effective ASR resolution, and scrubbed when
Provider config is saved again. There is no Provider-to-Language-Profile
auto-migration.

## Modified Files

- `src/config/provider_store.py`
- `tests/test_provider_language_profile_asr_boundary.py`
- `acceptance/m8_9_provider_language_profile_asr_boundary.md`

## Function Entry Mapping

The plan names matched existing repository entry points:

- Provider effective config: `provider_store.resolve_provider_config()`
- Batch effective ASR config: `batch_worker.main()` with a monkeypatched
  `BatchPipeline`
- Web single-file job assembly: `job_api.create_job()`

No public production API was changed to make tests easier.

## Validation

Preflight:

```powershell
git status --short --untracked-files=all
git log --oneline -5
git tag --list "m8.8*"
git diff --check
```

Result: tracked worktree was clean before edits; M8.8 remained the current tip;
M8.8 tags were present; `git diff --check` passed. Existing untracked audit zips
and `project_evaluation_report.md` were left untracked.

Focused tests:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_provider_language_profile_asr_boundary.py -q
```

Result: `6 passed`.

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_provider_language_profile_asr_boundary.py tests\test_effective_translation_config.py tests\test_language_profile_glossary.py -q
```

Result: `11 passed`.

Import smoke:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

Result: `imports ok`.

Full test suite:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests -q
```

Result: passed.

Diff and status checks:

```powershell
git diff --check
git diff -- src tests acceptance
git status --short --untracked-files=all
```

Result: `git diff --check` exited 0. `git status` showed only intended
M8.9 tracked/untracked edits plus the pre-existing untracked audit zips and
`project_evaluation_report.md`.

## Guardrails Confirmed

- `transcribe_to_srt()` core behavior was not modified.
- Batch and Web job flow were not restructured.
- Language Profile ASR fields remain unchanged.
- Provider config, templates, and example config remain LLM-only.
- No release artifact was modified.
- No M9 work was started.
- No tag was created or pushed.
