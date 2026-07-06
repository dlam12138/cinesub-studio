# M8 Closeout — Stability and Configuration-Boundary Phase

## 1. Summary

M8 is closed after runtime environment hardening, smoke rerun evidence, audit cleanup, and Provider / Language Profile ASR ownership clarification.

This milestone is a documentation and verification boundary only. No source code or pipeline behavior was changed as part of this closeout.

## 2. Completed Scope

The following M8 work is reflected in the repository history up to the closeout tip:

- **Runtime environment fix and smoke rerun** (M8.8): Rebuilt `.venv` with portable Python 3.12.10, fixed import chain, and reran segment ASR smoke.
- **Audit package refresh and external audit evidence** (M8.8): Audit zip files generated; intentionally remain untracked.
- **Cleanup of temporary audit files**: No build folders or temporary artifacts are staged.
- **Provider / Language Profile ASR boundary clarification** (M8.9): Removed ASR ownership from Provider; Language Profile is the canonical owner of ASR defaults.
- **Legacy Provider ASR fields tolerated and ignored**: Existing provider configs may still contain `whisper_model` / `whisper_device`; these fields are ignored and will be scrubbed on future provider writes.
- **Provider remains LLM-only**: Provider config owns API Key, API Base, and LLM model selection only.
- **Language Profile remains the owner of ASR defaults**: `whisper_model`, `whisper_device`, and related ASR parameters live in Language Profile.
- **CLI ASR arguments remain highest priority**: Explicit CLI arguments override both Language Profile and built-in defaults.

## 3. Final ASR Configuration Rule

The precedence for ASR configuration is fixed as:

```text
CLI explicit ASR args > Language Profile ASR settings > built-in defaults
```

Provider config does not own ASR settings.
Legacy provider `whisper_model` / `whisper_device` fields are ignored and scrubbed on future provider writes.

## 4. Validation

### 4.1 Git diff check

```powershell
git diff --check
```

**Result:** No whitespace errors. Clean.

### 4.2 Import smoke check

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
. .venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

**Result:**

```text
imports ok
```

All core, pipeline, config, web, and tools modules import successfully.

### 4.3 Pytest suite

```powershell
. .venv\Scripts\python.exe -B -m pytest tests
```

**Result:**

```text
245 passed in 7.27s
```

All 245 tests pass. No failures, no skips reported.

## 5. Files Intentionally Not Included

The following files are present in the worktree but are **not** part of the closeout commit:

- `project_evaluation_report.md` — untracked; not part of the milestone deliverable.
- `audit/external_audit_m7_*.zip` — prior milestone audit artifacts; remain untracked.
- `audit/external_audit_m8_*.zip` — M8 audit artifacts; remain untracked.
- `audit/m8_8_build/` — build output directory; not committed.
- `audit/external_audit_m8_8/` — extracted audit contents; not committed.
- Runtime outputs (`output/`, `work/`, `logs/`, `uploads/`, `.cache/`, etc.).
- Screenshots in `acceptance/screenshots/`.
- Caches and temporary scripts.

## 6. Known Limitations

The following capabilities are intentionally deferred to later milestones:

- M8 does not productize the Web UI.
- M8 does not add queue/history UI.
- M8 does not add runtime/model management UI.
- M8 does not add desktop packaging.
- M8 does not implement Chinese dubbing, TTS, voice cloning, audio mixing, or lip-sync.
- M8 does not introduce database storage.

## 7. M9 Handoff

The next phase is **M9 Web UI Productization MVP**.

M9 should start from this closeout tip and focus on making the existing Web subtitle workflow understandable and usable for non-developer users. M9 should not reopen M8 runtime/configuration-boundary work unless a regression is found.

## 8. Repository State at Closeout

- **Tip commit:** `7e1003a` — `m8.9: clarify ASR config ownership`
- **Branch:** `milestone6.10-local-storage-hygiene`
- **Note:** The closeout commit was created on branch `milestone6.10-local-storage-hygiene`.
- **Worktree:** Clean except for intentionally untracked files listed in Section 5.
- **Existing M8 tags:**
  - `m8.1-segment-routing-opt-in`
  - `m8.4-segment-apply-guardrails`
  - `m8.5-segment-apply-smoke-ux`
  - `m8.7-authorized-smoke-env-blocked`
  - `m8.8-runtime-env-fix-and-smoke-rerun`
  - `m8.8-runtime-env-smoke-rerun`
