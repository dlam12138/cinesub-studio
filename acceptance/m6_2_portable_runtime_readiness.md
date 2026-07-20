# M6.2 Portable Runtime Readiness

## Summary

M6.2 prepares CineSub Studio to understand both the current source checkout
layout and a future portable release layout. It does not build or ship a
portable runtime.

## Root semantics

- `project_root`: writable runtime root containing `input/`, `output/`,
  `work/`, `logs/`, `models/`, `.cache/`, and `tools/`.
- `app_root`: application/code root containing `src/`, `web/`, `config/`, and
  `scripts/`.
- `src_root`: Python source root.
- `runtime_root`: future release `runtime/` root when a release layout is
  explicitly detected.

Source layout:

```text
repo/
  src/
  web/
  input/
  output/
  work/
  logs/
  .venv/
```

Future release layout:

```text
release/
  app/
    src/
    web/
  runtime/
    python/
  tools/
    ffmpeg/
  input/
  output/
  work/
  logs/
```

## Guardrails preserved

- `runtime_paths.py` is side-effect free; importing or resolving paths does not
  create directories, write files, download anything, or mutate environment
  variables.
- Path resolution is based on known file/module locations, not the current
  working directory.
- Release layout detection is conservative and requires an explicit marker;
  a plain `runtime/` directory in a source checkout remains source layout.
- No `start_app.bat` was added. Future launcher lookup order is documented as
  `runtime/python/python.exe -> .venv/Scripts/python.exe -> clear failure
  guidance`.
- Pipeline and Web subprocesses still use `sys.executable -B`.
- FFmpeg lookup priority is preserved.
- Provider schema, Language Profile schema, pipeline retry semantics, and ASS
  reservation behavior are unchanged.
- No `runtime/python/`, release archive, wheelhouse, model cache, FFmpeg/CUDA
  binary, or generated runtime payload is included.

## Validation results

- Targeted runtime path/Web env/pipeline/FFmpeg/diagnostics tests: `24 passed`.
- Full pytest: `60 passed`.
- Basic import check: `imports ok`.
- `subtitle_translate.py --self-test`: passed.
- `quality_checker.py --self-test`: passed.
- `runtime_env.py diagnostics`: returned JSON successfully with
  `runtime_layout=source`, `diagnostic_summary.status=warning`, and
  `ffmpeg_source=bundled`.
- `batch_worker.py --scan`: passed, no pending files.
- `batch_worker.py --status`: passed, 8 completed tasks reported.
- `batch_worker.py --review`: returned `1` with a valid review summary and was
  treated as `issues_found`.
- `scripts/smoke_test.ps1`: passed; Web smoke reported
  `home=200 diagnostics=200`.
