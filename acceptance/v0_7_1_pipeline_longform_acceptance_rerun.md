# v0.7.1 Stage 2 Longform Pipeline Post-Fix Rerun

- Date: 2026-07-27
- Branch: `codex/stage2-worker-lifecycle-fix`
- Runtime commit: `adbf687`
- Final fix commit: `b090b8a`
- Initial Stage 2 run: **Fail - release blocker**
- Post-fix real-media scenarios: **Pass**
- Stage 2 gate: **Pass**
- Release blocker: **false**
- Allows Stage 3: **Yes**
- Allows Release Prep: **No**

The final fix commit differs from the runtime commit only by a targeted lint
annotation on an import; executable behavior is unchanged.

## Anonymous Sample

| Item | Observed |
| --- | --- |
| Sample ID | `sample-long-02` |
| Duration | 45.1 minutes |
| Container | MKV |
| Source hash prefix | `f296414a2968` |
| Model | `large-v3` |
| Device / compute | CUDA / float16 |
| Model loading | `local_files_only=True` |
| Translation | Current configured provider |

The rerun used fresh input identities and independent run IDs. Media, subtitle
text, complete hashes, request content, local paths, and raw logs remain in
ignored private storage.

## Real-Media Results

| ID | Result | Evidence |
| --- | --- | --- |
| S1 | Pass | The read-only plan returned 200 with no blocker and a stable plan/config identity. Task-state and output fingerprints were unchanged; no Worker started. |
| S2 | Pass | An independent 45.1-minute run completed ASR, translation, quality checking, final subtitle/report output, and input archive. The task and run record reached `completed`, `finished_at` was set, return code was 0, and no invalid-handle error appeared. |
| S3 | Pass | Two 45-second inputs in different directories shared one filename stem. They produced two task identities, two output stems, disjoint output paths, completed task states, and a completed run record without cache or output collision. |
| S7 | Pass | A fresh longform Worker entered ASR, survived forced Web termination with the same PID/creation FILETIME and lease, and continued writing its run-scoped logs. Restarted Web recognized the live Worker, rejected a duplicate run with 409 `pipeline_busy`, and the original Worker completed every stage with return code 0 and a terminal run record. |

The S2 and S7 longform runs were independent. No second Worker was observed,
and neither run contained `OSError: [Errno 22] Invalid argument`.

## Automated Evidence

| Scope | Result |
| --- | --- |
| Lifecycle and preflight suites | 55 passed |
| Full pytest | 526 passed |
| Import, syntax, translation, and quality self-tests | Pass |
| Web smoke (`/` and diagnostics) | Pass |
| `git diff --check` | Pass |
| Changed Python files Ruff regression | None: 30 current / 30 parent findings |
| Repository-wide Ruff | 182 pre-existing findings |
| Parent repository-wide Ruff | 182 findings |
| Repository-wide Ruff delta | 0 |

Repository-wide Ruff debt predates this branch and is unchanged from the
parent baseline. Stage 2 uses a no-regression lint policy for this scoped
lifecycle fix: the changed-file finding set remains 30 in both the parent and
current revisions, and the repository-wide finding count remains 182. The
existing lint debt is tracked separately and is not a Stage 2 release blocker.

## Privacy Audit

- Public evidence contains only anonymous media metadata, truncated hashes,
  scenario outcomes, test counts, commit identifiers, and sanitized errors.
- No real filename, private absolute path, username, subtitle text, prompt,
  API key, Authorization header, session token, complete media hash, or raw
  provider request is included.
- The original failed acceptance reports remain unchanged.

## Decision

The two original lifecycle release blockers are fixed. Independent S1, S2,
S3, and S7 real-media scenarios all pass, permanent regression coverage
passes, both Windows CI runs pass, and the change introduces no Ruff
regression. Stage 2 passes and Stage 3 may begin. Release preparation remains
out of scope until the ASR and translation quality campaign is completed.
