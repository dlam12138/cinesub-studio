# v0.7.1 Stage 2 Longform Pipeline Acceptance

- Date: 2026-07-27
- Branch: `codex/pipeline-longform-acceptance`
- Frozen source baseline: `252f75c7bea15070fd8a644694c0dd74ce3ade66`
- Result: Conditional pass

## Summary

Stage 2 started from the post-fix baseline after three pre-freeze PRs were merged:

- PR #13: Web path/log redaction canary.
- PR #14: Pipeline stage signature completeness.
- PR #15: Public plan hash/path redaction.

The local runtime was snapshotted before execution. The `large-v3` ASR model was downloaded from `hf-mirror` into the project `models/` directory and validated through the project locator with `local_files_only=True`; CTranslate2 reported the model directory as valid.

Formal private media and generated subtitles remain in ignored local directories. This public report contains only anonymized scenario labels, tool results, durations, and artifact hashes.

## Environment

| Item | Observed |
| --- | --- |
| Device | CUDA |
| Compute type | float16 |
| Model | `Systran/faster-whisper-large-v3` |
| Model source | project `models/` directory |
| FFmpeg | bundled project FFmpeg |
| Web bind | `127.0.0.1` |
| Network model loading | disabled during ASR runs (`local_files_only=True`, offline env flags set) |

## Scenario Results

| ID | Scenario | Evidence | Result |
| --- | --- | --- | --- |
| S1 | Real cold run, `large-v3`, CUDA/float16, no translation | 1 private MP4, ASR completed in 49.01s; source SRT SHA256 `c7a21bb5b61402264e75a0ceef9832d8c106eff26b542b221636c7353a231602`; language JSON SHA256 `99b75aedec83811a40a26517b02e4fb154a708764d07c2dd2af08495997a210a` | Pass |
| S2 | Same config rerun skips completed task | Second run returned completed 0 / skipped 1 in 0.43s | Pass |
| S3 | Same-stem collision, single run with two inputs | Two same-named private files completed; output stems were `movie-c50ccea6b556` and `movie-cf82e425ac4e`; both source SRT hashes match expected identical input content | Pass |
| S4 | Stage reuse/signature matrix | `tests/test_pipeline_offline_e2e.py` passed 13 tests, including translation knobs, quality-only threshold, translation length threshold, reserved ASS, final-output-only rebuild, and skip | Pass |
| S5 | Translation failure/retry variants | Offline e2e retry test passed; malformed/timeout style reliability paths remain covered by existing translation reliability tests in full suite | Pass |
| S6 | Unowned output collision and legacy state safety | `tests/test_pipeline_longform_reliability.py` passed 31 tests | Pass |
| S7 | Service restart/live worker recognition | `tests/test_pipeline_longform_reliability.py` passed service restart PID/filetime coverage | Pass |
| S8 | Stale run record after crash | `tests/test_pipeline_longform_reliability.py` passed stale/dead worker record coverage | Pass |
| S9 | Global run lock and worker lease contention | `tests/test_pipeline_longform_reliability.py` passed cross-process lock/lease coverage | Pass |
| S10 | Retry-failed task scope and zero-write validation | `tests/test_pipeline_preflight_consistency.py` passed 14 tests | Pass |
| S11 | Archive identity preservation | `tests/test_pipeline_longform_reliability.py` passed archive metadata coverage | Pass |
| S12 | Completed archive/output contract | Existing longform/offline coverage passed; no real translated archive run was performed in this public pass | Conditional |

## Additional Validation

| Check | Result |
| --- | --- |
| Full pytest | Pass |
| Import smoke | Pass |
| `subtitle_translate.py --self-test` | Pass |
| `quality_checker.py --self-test` | Pass |
| `start_web.ps1 -Smoke -NoBrowser -NonInteractive` | Pass |
| `node --check desktop/main.js`, `preload.js`, `launch.js` | Pass |
| `git diff --check` | Pass |
| HTTP `/` | 200 |
| HTTP `/api/runtime/diagnostics` | 200 |
| DeepSeek smoke | Pass, `deepseek-v4-flash`, response content present |

## Privacy Audit

- No private media, transcript text, OCR output, API key, or absolute local path is included in this report.
- Private runtime snapshots, model download attempts, media copies, logs, and generated subtitles remain under ignored directories.
- Public Web plan/scan responses now expose only hash prefixes and path-free task previews.

## Residual Risk

The final result is conditional because only S1-S3 were re-executed against private real media in this pass. S4-S12 were validated through the permanent automated reliability suites on the same frozen source baseline, plus HTTP and DeepSeek smoke checks. A future fully manual Stage 2 pass can rerun all 12 scenarios against long private media end to end, including a real translated archive workflow.
