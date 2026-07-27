# v0.7.1 Pipeline Longform Lifecycle Fix And Rerun

## User Goal

Fix the two Stage 2 lifecycle release blockers, add permanent regression
coverage, rerun independent S1, S2, S3, and S7 real-media scenarios, and
publish only anonymous evidence. Do not change ASR/translation quality,
start Stage 3, prepare a release, create a tag, or create a release.

## Facts And Evidence

- The initial acceptance evidence was merged through PR 16 and remains a
  `Fail - release blocker` historical record.
- Web launched the Worker with piped stdout/stderr and drained the pipe in the
  Web process. Terminating Web invalidated the Worker's inherited output path.
- The Worker wrote a terminal run record only on normal control flow; escaped
  exceptions could leave it at `running`.
- Windows process recognition compared PID and creation FILETIME but did not
  check `GetExitCodeProcess`, so an exited process object could appear live.
- The post-fix real-media rerun used anonymous sample `sample-long-02`, a
  45.1-minute MKV with hash prefix `f296414a2968`.
- S1, S2, S3, and S7 all passed. S2 and S7 were independent longform runs;
  S3 used two 45-second same-stem inputs.

## Decisions

- Use one shared Web Worker launcher with DEVNULL stdin and run-scoped
  stdout/stderr files whose parent handles close immediately after `Popen`.
- Sanitize Worker output and suppress subtitle issue details in Worker summary
  logs while preserving private quality reports.
- Add an outer Worker terminal guard and make existing terminal statuses
  idempotent.
- Treat Windows PID/FileTime identity as live only when the exit code is
  `STILL_ACTIVE`; atomically mark confirmed dead non-terminal records stale.
- Keep the Stage 2 gate at Fail because the required full ruff command still
  reports 182 pre-existing findings, the same count as the parent commit.

## Operations

- Created and merged the failed-evidence PR, updated main, and created
  `codex/stage2-worker-lifecycle-fix`.
- Added subprocess, parent-termination, terminal-record, dead-process, live
  process, special-terminal, and privacy regression tests.
- Updated Worker spawning, run-record lifecycle handling, process identity,
  API recovery, and quality-summary logging.
- Created fix commit `b090b8a`; the real-media runtime commit `adbf687`
  differs only by a targeted lint annotation.
- Ran fresh S1, S2, S3, and S7 inputs through the current configured provider,
  local `large-v3`, CUDA/float16, and unchanged quality settings.
- Added the approved anonymous Markdown, JSON, CSV, and thinking evidence plus
  exact `.gitignore` allowlist entries.

## Validation

- Lifecycle and preflight suites: 55 passed.
- Full pytest: 526 passed.
- Imports, JavaScript syntax, subtitle translation self-test, quality checker
  self-test, Web smoke, and `git diff --check`: passed.
- S2 completed all stages, outputs, report, archive, task state, and run
  record with return code 0.
- S3 produced two distinct task IDs, output stems, and cross-task output sets.
- S7 preserved Worker identity and lease across forced Web termination,
  restarted Web recognized the Worker, duplicate launch returned 409, logs
  grew, and the original run completed with return code 0.
- No real path, filename, username, complete hash, subtitle text, prompt,
  request body, API key, Authorization header, or session token is present in
  public evidence.
- Full ruff: 182 findings; parent commit full ruff: 182 findings.

## Unresolved And Next

- The repository-wide ruff baseline must be cleaned or the Stage 2 policy must
  explicitly adopt a no-new-findings rule before Stage 2 can pass.
- Until that gate is resolved: Stage 2 Fail, release blocker true, Stage 3 No,
  Release Prep No.
- No ASR, translation, resegmentation, glossary, provider, quality-threshold,
  UI, packaging, version, tag, or release work was performed.
