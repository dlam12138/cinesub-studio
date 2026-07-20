# M6.5 Portable Runtime Smoke

## Summary

M6.5 attempts to run the M6.3/M6.4 portable release layout with a real local
portable Python runtime. On this machine, the smoke is blocked before release
layout generation because `tools/python/python.exe` is not present.

This is recorded as M6.5 evidence rather than hidden or substituted with the
source `.venv`: the goal of this milestone is to prove what the ignored local
portable runtime input can actually do.

## Checkpoint

- M6.4 checkpoint tag: `m6.4-release-slimming`
- Tag target: `9f298542f6cf3448e1c3f2f786fcaefbc04a61fc`
- M6.5 branch: `milestone6.5-portable-runtime-smoke`
- Branch was created from `m6.4-release-slimming`.
- `project_evaluation_report.md` remained untracked and was not staged.
- Git directive marker scan returned no matches before the M6.5 commit.

## Portable Python input

- Expected input: `tools/python/python.exe`
- Actual result: missing
- `tools/` currently contains local ignored runtime directories for `cuda` and
  `ffmpeg`, but not `python`.
- `tools/python/` is ignored by `.gitignore` and must not be committed.
- Portable Python source: not supplied on this machine.
- Portable Python version: not available.
- Portable Python dependency readiness: not available.
- Portable torch / whisper readiness: not available.
- Model inference was not tested.

For context only, the source development environment is not a portable runtime:

- `.venv` Python version: `3.13.3`
- `.venv` module probe: `faster_whisper=present`, `torch=missing`

## Builder attempt

Command:

```powershell
.\.venv\Scripts\python.exe -B .\scripts\build_portable_release.py --force
```

Result:

```text
[ERROR] Portable Python runtime is missing: <repo>\tools\python\python.exe. Provide tools/python/python.exe or pass --python-runtime.
```

No `dist/cinesub-portable/` directory was generated.

## Release smoke results

Because the builder stopped before creating the release directory:

- `dist/cinesub-portable/start_app.bat`: not run.
- Web home smoke: skipped.
- `/api/runtime/diagnostics`: skipped.
- `/api/translation/effective-config`: skipped.
- `runtime_layout=release`: not verified.
- `ffmpeg_source=bundled`: not verified in release layout.
- `release_root/config/` write behavior: not runtime-verified.
- Artifact download behavior: skipped.
- Pipeline review `issues_found`: skipped.
- Port fallback was not needed because no release Web process was started.

No real Provider API key was copied, written into a release directory, logged,
or committed.

## Local guardrail checks

- `dist/`, `tools/python/`, `models/`, `.cache/`, `output/`, `work/`, and
  `logs/` are ignored runtime/artifact paths.
- `tools/ffmpeg/bin/ffmpeg.exe` exists locally and reports
  `ffmpeg version 8.1.2-essentials_build-www.gyan.dev`.
- No generated release directory, model cache, zip, runtime Python, output,
  work, or log artifact is included in this milestone.

## Outcome

M6.5 did not expose a confirmed source/path bug in the release layout. It
confirmed that a real portable Python runtime is still required before the
release-layout smoke can proceed past builder startup.

Next attempt should supply a complete ignored `tools/python/` runtime, including
the project dependencies needed for Web startup. If that runtime is minimal and
missing ML dependencies, record the exact missing dependency while still avoiding
model downloads and inference.
