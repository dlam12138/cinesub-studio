# Acceptance — Windows Pipeline Lock / Lease / Handoff Stability (Issue #10)

## Environment

| Item | Value |
| --- | --- |
| base SHA | `2c317d256a18c0ad6ed8ded4534b481fc3b59234` (main, includes PR #11) |
| fix SHA | branch `codex/windows-lock-lease-stability` (PR pending merge) |
| Branch | `codex/windows-lock-lease-stability` |
| OS | Windows 11 (build 10.0.26100) |
| Python | 3.12.10 (project `.venv`, base interpreter `tools/python`) |
| pytest | 9.1.1 |

## Baseline failure frequency (unmodified code)

Three targeted tests, run as-is:

| Environment | Result | Observed assertions |
| --- | --- | --- |
| Shell **without** repo `PYTHONPATH` | **30 / 30 rounds failed** (~0.7 s/round) | `assert '' == 'locked'`, `assert '' == 'leased'`, `assert None == 0` |
| Shell **with** CI-equivalent `PYTHONPATH` (`src/core;src/pipeline;src/config;src/web;src/tools`) | 0 / 30 rounds failed | — |

Manual execution of the exact child command used by test 1:

* without `PYTHONPATH`: `ModuleNotFoundError: No module named 'pipeline_reliability'` on stderr, exit code 1 — the stderr pipe was never drained by the test, so the assertion only saw `''`;
* with `PYTHONPATH`: prints `locked`, exit code 0.

No leftover Python processes after failing runs (children crashed at import;
`child.wait()` reaped them). Lock files created by the parent side of the tests
are 2 bytes (`pipeline_run.lock`, offsets 0/1 both extended); test 1's child
never created its lock file because it died before executing lock code.

## Root cause

**Test bootstrap + synchronization defects, not a runtime lock bug.**

1. **Ambient `PYTHONPATH` dependency (primary cause).** The tests spawned
   `python -c "<code>"` children that import `pipeline_reliability`. pytest's
   `pythonpath` ini option only patches the pytest process' `sys.path`; it
   never sets the `PYTHONPATH` environment variable, so children inherit
   whatever the calling terminal happens to have. GitHub's Windows CI sets
   `PYTHONPATH: src/core;src/pipeline;src/config;src/web;src/tools` at the job
   level (`.github/workflows/windows-ci.yml`), hence always-green there; local
   shells vary (the validation machine's ambient `PYTHONPATH` pointed at an
   unrelated vendor directory), hence "occasional" failures. Production is
   unaffected: real workers receive an explicit `PYTHONPATH` via
   `build_child_process_env`.
2. **Non-diagnostic assertions.** Bare `stdout.readline() == "locked"` turns
   import failure, crash, EOF and protocol error into the identical message
   `'' == 'locked'`; the child stderr pipe was never read.
3. **Fixed-sleep synchronization.** Holder/worker liveness was guessed with
   `time.sleep(2)` instead of explicit handshakes, and the background test
   asserted `returncode == 0` with no bounded wait and no visibility into
   `PIPELINE_TASK["error"]`.

### Runtime semantics were verified, not assumed

* **Windows byte-range locks are per-handle on this platform.** Probe: parent
  holds offset 0 (handle A) + offset 1 (handle B); after `release(B)`
  (LK_UNLCK + close), a separate process still sees offset 0 HELD. The
  documented "closing any handle releases all of a process' locks" behavior
  did not occur on Windows 11 10.0.26100 — the launch gate therefore really
  stays held during the production handoff window.
* **Same-process re-lock is correctly rejected** (`acquire()` returns False),
  which validates in-process launcher probes used by the gap regression test.
* `close_fds=True` (default) is used by every `Popen` in `src/`; the
  inheritable marking in `PipelineRunLock.acquire()` is never actually
  inherited by children. No handle-inheritance leak path exists.

### One minimal runtime hardening (evidence-based)

`run_pipeline_background` left `PIPELINE_TASK["returncode"] = None` on its
exception path even when the spawned worker already had a known exit code
(e.g. worker crashed before writing the handoff ack → `terminate()`+`wait()`
ran, then `RuntimeError`). Callers could not distinguish "still running" from
"failed at startup". The fix records `process.poll()` when available on the
exception path. No lock, flow, or preflight semantics changed.

## Changes

| File | Kind | Summary |
| --- | --- | --- |
| `tests/_pipeline_process_helper.py` | new test helper | Child side: self-bootstraps import paths relative to the repo, emits JSON-line events (`started/waiting_for_lease/locked/blocked/leased/lease_failed/acked/released/acquired/error`), accepts `{"cmd":"release"}` on stdin, never depends on ambient `PYTHONPATH`/cwd. Parent side: `PipelineProbe` with reader threads, monotonic deadlines, and diagnosable `ProbeError`s (pid, returncode, events, stdout/stderr tails). Handles the Windows venv launcher split (venv `python.exe` spawns the real interpreter and forwards its exit code) with staged cleanup: natural exit → stdin EOF → terminate → kill. |
| `tests/test_pipeline_longform_reliability.py` | test rewrite + regressions | 3 target tests rewritten to the explicit handshake protocol (no fixed sleeps); 7 new regression tests (§10.1–10.4 coverage). |
| `src/web/pipeline_api.py` | runtime hardening (7 lines) | Exception path of `run_pipeline_background` records the observed worker exit code so terminal state is never stuck at `returncode=None` when the code is known. |
| `.gitignore` | hygiene | Ignore `.tmp-lock-diagnostics/`; allow `acceptance/windows_pipeline_lock_lease_stability.md`. |

## Why this is a test bug (plus one observability hardening)

* The failing assertions were caused by children dying at import in shells
  without `PYTHONPATH` — 30/30 reproduction without, 0/30 with. Production
  workers never depend on ambient `PYTHONPATH`.
* The lock/lease/handoff runtime was probed directly and behaves correctly on
  this platform (gate held during handoff; OS releases locks on abrupt child
  exit; per-handle lock semantics).
* The only production change records an already-observed exit code on the
  failure path; it cannot alter success behavior (locks are still released in
  `finally`, statuses unchanged, preflight untouched).

## Stress validation (local Windows, adversarial shell: `PYTHONPATH` unset)

| Run | Result |
| --- | --- |
| `test_pipeline_lock_contends_across_real_processes` × 100 | **0 failures** (92 s) |
| `test_worker_lease_handoff_blocks_new_launcher` × 100 | **0 failures** (106 s) |
| `test_background_runner_completes_explicit_worker_lease_handoff` × 100 | **0 failures** (110 s) |
| `tests/test_pipeline_longform_reliability.py` (full file) × 20 | **0 failures** (124 s; first loop 2/20 due to an over-strict probe in the gap test — fixed, then restarted per the no-reuse rule) |
| Related suites (`test_pipeline_longform_reliability.py` + `test_pipeline_preflight_consistency.py`) | **pass** (43 tests) |
| Full `pytest` | **pass** (~20 s, whole suite) |
| Leftover probe processes after validation | none |

## GitHub Windows CI

| Run | Result |
| --- | --- |
| initial push run (30064804234) | **success** (1 m 06 s) |
| initial PR run (30064882656) | **success** |
| manual rerun of PR run (30064882656) | **success** |

## Known risks

* If a future Windows build revives the legacy "closing any handle releases
  all locks" semantics, the gap regression test
  (`test_lease_handoff_has_no_observable_gap`) fails loudly instead of
  silently allowing concurrent pipelines.
* `PipelineRunLock.acquire()` still marks handles inheritable although no
  code path inherits them (all `Popen` calls use default `close_fds=True`).
  Dead behavior, left untouched per minimal-change scope.

## Issue #10 closure

All gates green: three targeted tests keep their original names and pass for
real (100/100 each), no skip/xfail/relaxed assertions, full-file 20/20,
related suites and full pytest pass, no leftover processes, locks and leases
release on failure paths, child startup failures are diagnosable, and both
Windows CI runs (initial + manual rerun) passed. **Issue #10 can be closed
by PR #12.**
