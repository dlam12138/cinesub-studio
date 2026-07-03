# M6.8 Release Candidate Audit & Notes

## Summary

M6.8 turns the already runnable `m6.7-rc1` portable zip into an auditable trial
candidate. This milestone does not rebuild the package, change runtime behavior,
or introduce PyInstaller. It records the existing artifact, checksum, launch
result, package exclusions, and user-facing startup path.

## Checkpoint Tag

- Tag: `m6.7-release-candidate-packaging`
- Target commit: `859de77`
- Tag action: created only if missing; no force-retagging
- Commit guarded by tag check: if the tag had existed and pointed elsewhere,
  M6.8 would stop instead of moving it.

## Audited Artifact

Existing files audited by M6.8:

```text
dist/cinesub-portable-m6.7-rc1.zip
dist/cinesub-portable-m6.7-rc1.zip.sha256
```

Recorded artifact metadata:

```text
zip_bytes=245587506
sha256=0a0b0fb3e46b4fe73a28865002d05975b6e065365cf5463d916582ceb7abb6df
```

The zip SHA256 is stored outside the zip in the sidecar file. M6.8 did not
rerun the release builder and did not rewrite the zip.

## Extracted RC Result

M6.7 already validated the extracted package by launching from the release
directory with:

```text
start_app.bat
```

Observed release smoke result:

```text
home=200
diagnostics=200
effective_config=200
runtime_layout=release
python_source=project-portable-python
ffmpeg_source=bundled
```

Provider configuration was allowed to remain not configured. The RC startup
and diagnostics path do not require a Provider; paid translation does.

## Package Audit Notes

The package is expected to contain the application, portable Python runtime,
bundled FFmpeg, release metadata, and empty runtime placeholder directories.

Forbidden local or user data remains excluded:

```text
.git/
.venv/
.tmp/
dist/
tools/python/
tools/cuda/
config/providers.local.json
output/* user subtitles or reports
work/* intermediate files
logs/* runtime logs
uploads/* user media
.cache/* cache contents
models/* downloaded models
```

Empty top-level placeholders are allowed so the extracted package has a writable
runtime layout:

```text
input/
output/
work/
logs/
uploads/
models/
.cache/
config/
```

The package reports `leak_scan=passed` in `release_report.md`. Release metadata
uses release-relative paths and intentionally omits local absolute source paths,
Provider configuration, API keys, and large command output.

## User-Facing Notes

Portable RC path:

```text
unzip dist/cinesub-portable-m6.7-rc1.zip
cd cinesub-portable
start_app.bat
open http://127.0.0.1:7860
```

The Portable RC includes `runtime/python/` and does not need system Python.

Source/dev path:

```text
install system Python 3.10-3.12
.\install.ps1
.\start_web.ps1
open http://127.0.0.1:7860
```

The source/dev layout uses the project `.venv` and does need system Python.

## Known Limits

- No PyInstaller EXE is produced in M6.8.
- The RC zip does not include Whisper models, wheelhouse, CUDA DLLs, sample
  media, sample subtitles, or tests.
- Provider configuration is optional for Web startup and diagnostics, but
  required for actual translation.
- Mixed-language media is handled as one primary source language; split complex
  mixed-language material or use source subtitles when needed.
- SRT is the enabled subtitle output. ASS parameters remain reserved; requesting
  ASS should report that no `.ass` file was generated.
- Large runtime dependency layers come mainly from bundled FFmpeg and portable
  Python packages, as listed in `release_report.md`.

## Verification

Required M6.8 verification:

```text
git tag --list "m6*"
git show --stat m6.7-release-candidate-packaging
git status --short
.\.venv\Scripts\python.exe -B -m pytest tests/test_text_encoding_hygiene.py -q
```

Also verify that no Codex git directive marker strings are present in `HEAD`.

基础导入检查:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

Done conditions:

- `m6.7-release-candidate-packaging` exists and points to `859de77`.
- `README.md` distinguishes Portable RC from Source/dev startup.
- RC zip SHA256, size, startup result, and `runtime_layout=release` are recorded.
- Package exclusions, empty placeholder allowance, and known limits are recorded.
- M6.8 does not rebuild the zip or commit generated runtime/release artifacts.
- `project_evaluation_report.md` remains untracked.
