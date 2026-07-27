# v0.7.1 Stage 2 Longform Pipeline Acceptance

- Date: 2026-07-27
- Branch: `codex/pipeline-longform-acceptance`
- Runtime base: `252f75c`
- Acceptance start commit: `6f87b90`
- Evidence commit: `90eaccb`
- Result: **Fail - release blocker**
- Allows Stage 3: **No**
- Allows Release Prep: **No**

## Sample And Runtime

| Item | Observed |
| --- | --- |
| Sample ID | `sample-long-02` |
| Duration | 45.1 minutes |
| Container | MKV |
| Source hash prefix | `f296414a2968` |
| Model | `large-v3` |
| Device / compute | CUDA / float16 |
| Model loading | `local_files_only=True` |
| Translation provider | deterministic local stub |

The source was copied into the ignored private acceptance area and its full
hash matched the source. The original media was not moved, renamed, or
modified. This report does not evaluate ASR accuracy or translation quality.

## Live Evidence

| ID | Result | Evidence |
| --- | --- | --- |
| S1 | Pass | Two read-only plan requests returned 200 with identical classification and public hash prefixes. Filesystem metadata snapshots were unchanged; no task state, run record, output, archive artifact, or worker lease was created by plan. |
| S2 | Fail | The longform run was accepted and reached ASR. ASR completed in 225.46s and translation completed through 27 local requests, but quality checking failed with `OSError: [Errno 22] Invalid argument`. Finalization and input archive did not run, and the run record did not reach `completed`. |
| S3 | Not run | The strict S7 failure rule stopped the acceptance rerun before the same-stem live scenario. Previous evidence is not promoted as evidence for this rerun. |
| S7 | Fail | Web was terminated during ASR by killing only the listener. The same worker PID and creation FILETIME survived, retained the lease, and continued through ASR and translation. Restarted Web recognized the live run and a second run was rejected as busy. The inherited process I/O later became invalid during quality checking; the worker exited non-zero, task state became `failed`, while the run record remained `running`. |

The run ID, task ID, worker PID, worker creation FILETIME, plan fingerprint,
and effective-config hash remained semantically unchanged through the live
restart window. No second worker was launched. After the failure, all
acceptance Web, worker, monitor, and stub processes were removed.

## Automated Evidence

S4-S6 and S8-S12 remain permanent automated evidence rather than real-media
live reruns. The requested `tests/test_pipeline_stage_reuse_acceptance.py`
does not exist on this branch, so the repository's
`tests/test_pipeline_offline_e2e.py` was used for the stage-reuse matrix.

| Scope | Result |
| --- | --- |
| Longform, preflight, offline stage-reuse, privacy suites | 61 passed |
| Full pytest | 516 passed |
| Translation and quality self-tests | Pass |
| Web smoke | Pass |
| Electron JavaScript syntax checks | Pass |
| `git diff --check` | Pass |

Passing automated tests do not override the S7 live failure. The existing
service-restart tests validate PID/FILETIME recognition and lock behavior,
but they did not expose the inherited stdout/stderr failure or the
non-terminal run record observed with the real longform worker.

## Privacy Audit

- Public files contain no source path, real filename, movie title, complete
  source hash, transcript, prompt, API key, session token, or private log.
- Only the anonymous sample ID, rounded duration, container, and 12-character
  source hash prefix are published.
- Media, subtitles, complete fingerprints, run records, process trees, and
  raw logs remain in the ignored private acceptance area.

## Decision

S7 violates the strict acceptance rules because a post-restart stage failed,
the worker exited non-zero, finalization did not complete, and the run record
did not become terminal. Stage 2 therefore remains blocked. Do not begin
Stage 3, Release Prep, a version bump, release archive build, or tagging until
the process I/O and terminal-record behavior are fixed and S1/S2/S3/S7 are
rerun successfully.
