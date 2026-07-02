# M6.1 Maintainability & Release Hygiene

## Fixed / cleaned

- Created checkpoint tag `m6.0-first-run-readiness` at `662992c`.
- Created working branch `milestone6.1-release-hygiene` from the checkpoint tag.
- Audited tracked text files under `src/`, `web/index.html`, `README.md`, `acceptance/`, and `scripts/` for specific mojibake snippets.
- No confirmed mojibake was found in tracked user-facing source text; PowerShell display artifacts were not rewritten as source changes.
- Added encoding hygiene coverage for specific UTF-8-as-GB18030 mojibake snippets without using broad single-character markers.
- Consolidated Web child-process environment setup in `src/web/process_env.py` for:
  - project-local Hugging Face cache paths
  - Web subprocess `PYTHONPATH`
  - UTF-8 process environment
  - proxy environment removal
  - project CUDA environment injection
  - project-root path redaction
- Updated single-file and pipeline Web helpers to use the shared environment/redaction helper without changing command behavior.

## Guardrails preserved

- Backend pipeline subprocesses still use `sys.executable -B src\pipeline\batch_worker.py`.
- Review return code semantics are unchanged: a valid `--review` return code `1` is treated as `issues_found`, not as a command crash.
- No task state schema changes.
- No Provider schema changes.
- No Language Profile schema changes.
- `web/index.html` remains a single file; no npm, CDN, React, Vue, or frontend build step was introduced.
- Runtime output roots remain unchanged: `output/`, `work/`, `logs/`, `models/`, `.cache/`, and `tools/`.

## Validation results

- `git tag --list "m6.0-first-run-readiness"`: tag exists.
- Git directive marker grep check: no output.
- Targeted related tests: `16 passed`.
- Full pytest: `51 passed`.
- Basic import check: `imports ok`.
- `subtitle_translate.py --self-test`: passed.
- `quality_checker.py --self-test`: passed.
- `runtime_env.py diagnostics`: returned JSON successfully; `diagnostic_summary.status=warning`, `ffmpeg_source=bundled`.
- `batch_worker.py --scan`: passed, no pending files.
- `batch_worker.py --status`: passed, 8 completed tasks reported.
- `batch_worker.py --review`: returned `1` with a valid review summary and was treated as `issues_found`.
- `scripts/smoke_test.ps1`: passed; Web smoke reported `home=200 diagnostics=200`.

## Deferred to M6.2+

- Portable Python switching was not implemented.
- Portable release packaging was not implemented.
- PyInstaller / standalone EXE work was not implemented.
- Run History was not implemented.
- ASS output remains reserved for a future version.
