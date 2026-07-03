# M6.7 Release Candidate Packaging

## Summary

M6.7 extends the portable release builder from an unpacked prototype into a
release candidate package. The build now creates both:

- `dist/cinesub-portable/`
- `dist/cinesub-portable-m6.7-rc1.zip`

This milestone does not introduce PyInstaller or an EXE wrapper. The release
candidate remains a zip with a portable Python runtime and `start_app.bat`.

## Checkpoint

- M6.6 checkpoint tag: `m6.6-portable-python-runtime-prep`
- Tag target: `e0faa8653e03441effa2ff3da689426d3b513069`
- M6.7 branch: `milestone6.7-release-candidate-packaging`
- `project_evaluation_report.md` remained untracked and was not staged.

## Builder result

Command:

```powershell
.\.venv\Scripts\python.exe -B .\scripts\build_portable_release.py --force --version m6.7-rc1 --zip
```

Observed result:

```text
Portable release prototype: <repo>\dist\cinesub-portable
Python runtime: <repo>\dist\cinesub-portable\runtime\python
FFmpeg copied: yes
Release manifest: <repo>\dist\cinesub-portable\release_manifest.json
Release report: <repo>\dist\cinesub-portable\release_report.md
Release checksums: <repo>\dist\cinesub-portable\release_checksums.sha256
Release zip: <repo>\dist\cinesub-portable-m6.7-rc1.zip
Release zip SHA256: <repo>\dist\cinesub-portable-m6.7-rc1.zip.sha256
Copied files: 9819
Total bytes: 720145051
```

Manifest highlights:

```text
builder=m6.7-release-candidate-packaging
version=m6.7-rc1
payload_file_count=9819
payload_total_bytes=720145051
ffmpeg_copied=True
leak_scan=passed
checksums_count=9819
checksums_total=720145051
```

Zip sidecar:

```text
zip_bytes=245587506
sha256=0a0b0fb3e46b4fe73a28865002d05975b6e065365cf5463d916582ceb7abb6df
```

The zip byte size and zip SHA256 are intentionally recorded outside the zip in
`dist/cinesub-portable-m6.7-rc1.zip.sha256` and this acceptance note. They are
not embedded into the package manifest, avoiding a checksum cycle.

## Package guardrails

Zip structure check:

```text
top_levels=cinesub-portable
entries=10872
```

Rejected path checks:

```text
/.git/ 0
/.venv/ 0
/dist/ 0
/tools/python/ 0
/.tmp/ 0
/uploads/movie.mp4 0
/output/movie.zh.srt 0
/output/movie.quality_report.json 0
/output/movie.review_needed.srt 0
/config/providers.local.json 0
```

Package metadata rules:

- `release_manifest.json` and `release_report.md` use release-relative paths.
- `release_checksums.sha256` covers payload files and excludes itself plus
  generated release metadata.
- App/source content is still scanned for secret-looking values.
- Copied third-party files inside `runtime/python/` are excluded from content
  leak scanning to avoid false positives.
- Source `tools/python/` remains outside the zip; only copied
  `runtime/python/` is packaged.

## Extracted RC smoke

The RC zip was extracted to:

```text
<repo>\.tmp\m6_7_extract\cinesub-portable
```

Smoke was run from the extracted release directory via:

```text
.tmp\m6_7_extract\cinesub-portable\start_app.bat
```

Observed Web checks:

```text
home=200
diagnostics=200
effective_config=200
runtime_layout=release
project_root=<repo>\.tmp\m6_7_extract\cinesub-portable
python_source=project-portable-python
ffmpeg_source=bundled
```

The release Web process was stopped after validation, and port `7860` was clear.
Provider configuration was allowed to remain not configured; M6.7 validates
package startup and diagnostics, not paid translation.

## Verification

```text
tests/test_portable_release_builder.py: 13 passed
pytest -q: 77 passed
scripts/smoke_test.ps1: passed
```

`scripts/smoke_test.ps1` completed syntax/import checks, subtitle self-tests,
runtime diagnostics, pipeline scan/status/review, and Web smoke. Existing
pipeline review quality findings were reported and treated as non-blocking by
the smoke script.

## Outcome

M6.7 produced the first distributable release candidate zip that can be
extracted and launched independently from the source checkout. The package keeps
zip-level checksum metadata outside the zip, preserves release-relative package
metadata, and excludes local runtime artifacts and secrets.
