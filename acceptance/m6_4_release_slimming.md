# M6.4 Release Size & Artifact Slimming

## Summary

M6.4 adds size visibility and leak checks to the portable release prototype
builder. It does not create a zip, run PyInstaller, download large components,
delete local runtime data, or require a real portable Python runtime on this
machine.

## Checkpoint

- M6.3 checkpoint tag: `m6.3-portable-release-prototype`
- M6.4 branch: `milestone6.4-release-slimming`
- `project_evaluation_report.md` remained untracked and was not staged.

## Builder output

The portable release prototype now writes these generated files inside the
ignored `dist/` artifact:

- `release_manifest.json`
- `release_report.md`

Both files use release-relative paths such as `.`, `app`, and `runtime/python`.
They intentionally omit local absolute source paths, Provider configs, secrets,
and large command-output dumps.

The manifest/report summarize:

- copied file count
- total copied bytes
- largest copied files
- top-level file counts
- skipped/excluded categories
- FFmpeg copy status
- leak scan status

## Slimming and leak checks

- `tests/` is not copied by default.
- Provider local config, repo control files, runtime artifacts, subtitles,
  media files, quality reports, and review subtitles are blocked from the app
  payload.
- Empty release-root placeholders remain allowed for `output/`, `work/`,
  `logs/`, `.cache/`, `models/`, and `uploads/`.
- Content scanning rejects secret-looking values, including known sentinels,
  long `sk-` style keys, bearer tokens, and config-like secret values.
- Content scanning does not fail merely because source code contains field
  names such as `api_key`, `access_token`, or `refresh_token`.

## Remaining large dependency layers

Portable Python, wheelhouse, CUDA, models, and release archives remain separate
future release layers. M6.4 only reports and validates the prototype skeleton.

## Validation results

- Targeted portable release builder tests: passed.
- Full pytest: passed.
- Basic import check: `imports ok`.
- `subtitle_translate.py --self-test`: passed.
- `quality_checker.py --self-test`: passed.
- `runtime_env.py diagnostics`: passed with `runtime_layout=source`,
  `ffmpeg_source=bundled`, and `diagnostic_summary.status=warning`.
- Pipeline read-only checks:
  - `--scan`: passed, no pending files.
  - `--status`: passed.
  - `--review`: returned `1` with a valid review summary and was treated as
    `issues_found`.
- `scripts/smoke_test.ps1`: passed; Web smoke reported `home=200 diagnostics=200`.
