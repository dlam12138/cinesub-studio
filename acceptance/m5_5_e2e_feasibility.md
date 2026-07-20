# M5.5 E2E Feasibility Acceptance Report

## Environment

- Date: 2026-07-02
- OS: Windows local desktop
- Project path: `D:\Claude项目操作\电影翻译`
- Test input path: `D:\测试`
- Base branch/checkpoint: `milestone5`
- Base commit: `10f866c`
- Base tag: `m5-effective-config-glossary`
- M5.5 working branch: `milestone5.5-e2e-feasibility`

## Manual E2E Findings

- Web scan, status refresh, pipeline start, retry-failed trigger, runtime diagnostics, and effective translation config loaded successfully during manual validation.
- Pipeline UI showed 8 total tasks, 8 completed, 0 pending/running/failed, and 100% progress.
- Output artifacts existed under `output/source`, `output/bilingual`, and `output/reports`, but the UI did not expose clear per-task access.
- Review summaries were valid but Web treated CLI `returncode=1` as a command failure.
- Existing quality reports showed false-positive `llm_boilerplate` errors for normal dialogue such as `好的，很高兴认识你。`, `当然，别忘了。`, and `Sure.`-style lines.
- The French/English movie sample is mixed-language media, not a normal single-language sample.

## Fixed In M5.5

- Pipeline progress now exposes derived artifact metadata per task without changing task state JSON:
  `source`, `translated`, `bilingual`, `quality_report`, and `review_needed`.
- The Web UI now shows task artifact actions and copyable paths for completed/reviewed tasks.
- The new artifact endpoint resolves paths server-side from task state and only downloads files under project `output/`.
- `source_srt` outside `output/` remains visible/copyable but is not downloadable.
- Web review handling now treats `returncode=1` as `issues_found` only when stdout contains `Review summary` and either `Reports:` or `Review subtitles:`.
- CLI review return code behavior is unchanged.
- Windows console review output now avoids crashing on Unicode snippets.
- `llm_boilerplate` rules now target assistant meta-output instead of ordinary dialogue openings.

## Surfaced But Not Solved

- Mixed-language media can still be treated as one primary detected source language for the whole task.
- The French/English movie sample was detected as `en` for the whole task; `target_language=zh-CN` did flow correctly into translation.
- Current M5.5 does not implement per-segment language detection or multilingual ASR routing.
- Recommended user workarounds for mixed-language media:
  use a larger multilingual Whisper model, keep source language on auto instead of forcing one global language, or translate official source subtitles when available.
- Future milestone:
  `M7: Mixed-language ASR and Segment-level Language Detection`.

## Generated Artifacts Observed

- Source SRT examples:
  `output/source/*.small.srt`
- Bilingual Chinese SRT examples:
  `output/bilingual/*.small.bilingual.zh-CN.srt`
- Quality reports:
  `output/reports/*.small.quality_report.json`
- Review SRT files:
  `output/reports/*.small.review_needed.srt`

## Validation

- `pytest`: 46 passed
- Import check: `imports ok`
- `subtitle_translate.py --self-test`: passed
- `quality_checker.py --self-test`: passed
- `batch_worker.py --scan`: returned 0, no pending files
- `batch_worker.py --status`: returned 0, 8 completed tasks
- `batch_worker.py --review`: printed valid review summary and returned 1 because existing reports still contain quality errors
- Existing `output/reports/*.quality_report.json` files were generated before the `llm_boilerplate` rule refinement, so stored review summaries may still show old false positives until reports are regenerated.
- Web smoke on port 7861:
  home 200, runtime diagnostics 200, effective config 200, pipeline progress 8 completed tasks, first artifact download 200
- `web/index.html` mojibake marker counts:
  `妯=0`, `鎺=0`, `鍚=0`, `涓=0`, `瀷=0`, `U+FFFD=0`

## Debug Evidence / Logs

- Prefer persisted evidence when debugging after a test:
  `work/states/*.state.json`, `output/reports/*.quality_report.json`, `output/reports/*.review_needed.srt`, `output/**/*.srt`, `logs/pipeline.log` if present, and `acceptance/*.md`.
- Web UI action logs such as scan/status/review button output are not guaranteed to be persisted; they can disappear after page refreshes, service restarts, or console sessions that were not redirected to files.
- Diagnose from persisted artifacts first to decide whether an issue belongs to UI, pipeline, ASR, translation, or quality checker, then use user-provided UI logs as extra timeline context.
- Future M6/M6.x work can add a Run History / Test Session Log that persists Web scan/status/review/process/retry timestamps, commands, return codes, and stdout/stderr summaries.

## M6 Readiness

M6 can proceed after M5.5 is reviewed. The main E2E usability blockers found in M5.5 are addressed, and mixed-language media is explicitly documented as a future capability rather than an M5.5 fix.
