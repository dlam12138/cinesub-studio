# M6.9 External Trial Checklist / Release Notes Freeze

## Summary

M6.9 freezes the existing `m6.7-rc1` portable zip as the external trial
candidate. This milestone adds tester-facing instructions and records the
release-note freeze without rebuilding the package or changing runtime behavior.

## Checkpoint Tag

- Tag: `m6.8-release-candidate-audit`
- Target commit: `b149c72aab1709ff13a0758561ef315b08ceb67f`
- Tag action: create only if missing
- Guardrail: if the tag exists but points elsewhere, stop and do not force-retag

## RC Artifact

Existing files documented by M6.9:

```text
dist/cinesub-portable-m6.7-rc1.zip
dist/cinesub-portable-m6.7-rc1.zip.sha256
```

Recorded artifact metadata:

```text
zip_bytes=245587506
sha256=0a0b0fb3e46b4fe73a28865002d05975b6e065365cf5463d916582ceb7abb6df
```

M6.9 did not rerun the portable release builder, rewrite the zip, or update
generated release artifacts.

## Trial Scope

`TRIAL.md` is the canonical tester-facing guide for this RC. It covers:

- what the RC is
- Windows, disk, network, model, and Provider requirements
- unzip and `start_app.bat` startup
- opening `http://127.0.0.1:7860`
- Provider setup for translation
- putting media into `input/`
- scanning, running, checking status, and downloading outputs
- known limitations
- feedback details to collect
- API key and private media safety

`README.md` links to `TRIAL.md` from the Portable RC section without duplicating
the full external trial checklist.

## Non-Goals

- No CLI, Web API, Provider, Language Profile, pipeline, subtitle format,
  runtime, or packaging behavior changes.
- No PyInstaller EXE.
- No package rebuild.
- No new generated artifacts.
- No changes to `project_evaluation_report.md`.

## Known Limits

- The RC zip does not include Whisper models, CUDA offline packages, wheelhouse,
  sample media, or tests.
- Provider configuration is optional for startup and diagnostics, but required
  for translation.
- SRT is the enabled subtitle output. ASS remains reserved and should report
  that no `.ass` file was generated.
- Mixed-language media is handled as one primary source language; segment-level
  language detection is planned for a later milestone.
- Long or noisy media may need manual review using `output/reports/`.

## Verification

Required M6.9 verification:

```text
git tag --list "m6.8-release-candidate-audit"
git rev-parse m6.8-release-candidate-audit^{}
git status --short
run the Codex git directive marker scan against HEAD
.\.venv\Scripts\python.exe -B -m pytest tests/test_text_encoding_hygiene.py -q
```

Basic import check:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

Observed M6.9 results:

```text
m6.8-release-candidate-audit^{}=b149c72aab1709ff13a0758561ef315b08ceb67f
zip_sha256=0a0b0fb3e46b4fe73a28865002d05975b6e065365cf5463d916582ceb7abb6df
zip_bytes=245587506
Codex git directive marker scan against HEAD: no matches
tests/test_text_encoding_hygiene.py: passed
basic import check: imports ok
project_evaluation_report.md: remains untracked
```

Done conditions:

- `m6.8-release-candidate-audit` exists and points to `b149c72`.
- `TRIAL.md` exists and is suitable for external testers.
- `README.md` links to `TRIAL.md` from the Portable RC section.
- RC artifact name, SHA256, zip size, scope, non-goals, known limits, and
  verification are recorded.
- No runtime/application behavior changes are made.
- No zip rebuild or generated artifact commit is made.
- `project_evaluation_report.md` remains untracked.
- Encoding hygiene and basic import checks pass.
