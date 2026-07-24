# Acceptance — Windows Pipeline Lock / Lease / Handoff Stability (Issue #10)

## Environment

| Item | Value |
| --- | --- |
| base SHA | `2c317d256a18c0ad6ed8ded4534b481fc3b59234` (main, includes PR #11) |
| initial fix SHAs | `3544190` (runtime exit-code observability), `d147b3e` (test determinism) |
| P1 hardening SHAs | `1b4533b` (runtime worker tree kill), `c52ef89` (termination regression test) — validated code snapshot |
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

## Handoff-timeout worker termination (P1 review finding)

Review of the first PR iteration identified an uncovered failure path: when a
worker never writes the handoff ack, the server runs `process.terminate()`.
On Windows the project venv's `python.exe` is a forwarding launcher, so
`terminate()` kills only the launcher — and the `finally` block then releases
the launch gate while the real interpreter may still be alive.

Evidence gathered with instrumented probes (not assumed):

* After `terminate()` on the launcher, the real interpreter was **alive in
  100% of observations** (launchers and interpreters verified by pid +
  `GetExitCodeProcess == STILL_ACTIVE`).
* The interpreter then died in 4/5 follow-up observations when the worker's
  stdout pipe was closed during thread teardown — i.e. the previous
  production behavior relied on a **non-deterministic incidental side
  effect** (~80%) rather than a deliberate kill. The first version of the
  regression test passed only by landing in the 80%.

Fix (failure path only, driven by that evidence):

* `windows_terminate_process_tree(pid)` in `pipeline_reliability.py`:
  `CreateToolhelp32Snapshot` → BFS over the parent map → `TerminateProcess`
  descendants before the root. Used by `run_pipeline_background` on handoff
  timeout instead of `process.terminate()` (POSIX keeps `terminate()`).
* `_handoff_timeout: float = 10.0` private parameter so the regression test
  exercises the real path without waiting 10 s per run; production default
  unchanged.
* New regression test `test_handoff_timeout_terminates_real_worker_process`
  with a `hang-before-lease` probe child that reports the *real* interpreter
  pid, never touches the lock, and never acks. Asserts: deterministic
  terminal state (`returncode` not `None`, handoff error recorded), launcher
  pid dead, real interpreter pid dead (checked via exit code — creation
  filetime can keep resolving a dead process while inherited handles
  linger), both offsets re-acquirable, and a subsequent launcher succeeds.

Post-fix: orphan test 20/20 then 100/100; the direct probes now show both
processes dead immediately after the timeout kill.

## Changes

| File | Kind | Summary |
| --- | --- | --- |
| `tests/_pipeline_process_helper.py` | new test helper | Child side: self-bootstraps import paths relative to the repo, emits JSON-line events (`started/waiting_for_lease/locked/blocked/leased/lease_failed/acked/released/acquired/error/hanging`), accepts `{"cmd":"release"}` on stdin, never depends on ambient `PYTHONPATH`/cwd; `hang-before-lease` mode reports the real interpreter pid without touching the lock. Parent side: `PipelineProbe` with reader threads, monotonic deadlines, diagnosable `ProbeError`s (pid, returncode, events, stdout/stderr tails), staged cleanup (natural exit → stdin EOF → terminate → kill), and `process_exit_code`/`process_is_alive` liveness checks not fooled by lingering handles. |
| `tests/test_pipeline_longform_reliability.py` | test rewrite + regressions | 3 target tests rewritten to the explicit handshake protocol (no fixed sleeps); 8 new regression tests (§10.1–10.4 coverage + handoff-timeout worker-termination path). |
| `src/web/pipeline_api.py` | runtime hardening (failure path only) | (1) exception path records the observed worker exit code so terminal state is never stuck at `returncode=None`; (2) handoff timeout terminates the whole worker process tree instead of only the venv launcher; (3) `_handoff_timeout` private test parameter (default 10.0 s; production default unchanged). Success path, lock order and preflight semantics untouched. |
| `src/pipeline/pipeline_reliability.py` | runtime hardening (new utility) | `windows_terminate_process_tree(pid)`: snapshot-based descendant walk + `TerminateProcess` (descendants first), best-effort, Windows-only; used only by the handoff-timeout failure path. |
| `.gitignore` | hygiene | Ignore `.tmp-lock-diagnostics/` and `.learnings/`; allow this acceptance doc. |

## Why the change set is minimal and semantics-preserving

* The three flaky assertions were caused by children dying at import in
  shells without `PYTHONPATH` — 30/30 reproduction without, 0/30 with.
  Production workers never depend on ambient `PYTHONPATH`.
* The lock/lease/handoff runtime was probed directly and behaves correctly on
  this platform (gate held during handoff; OS releases locks on abrupt child
  exit; per-handle lock semantics).
* Both production diffs are confined to the failure path: recording an
  already-observed exit code, and killing the worker tree only when the
  handoff already failed and the worker is being discarded. Locks are still
  released in `finally`, statuses are unchanged, preflight is untouched, and
  the success path is byte-identical.

## Stress validation (local Windows, adversarial shell: `PYTHONPATH` unset)

All counts below were **restarted after the P1 handoff-timeout runtime
change** — pre-fix pass counts were discarded per the no-reuse rule.

| Run | Result |
| --- | --- |
| `test_pipeline_lock_contends_across_real_processes` × 100 | **0 failures** |
| `test_worker_lease_handoff_blocks_new_launcher` × 100 | **0 failures** |
| `test_background_runner_completes_explicit_worker_lease_handoff` × 100 | **0 failures** |
| `test_handoff_timeout_terminates_real_worker_process` × 100 | **0 failures** |
| `tests/test_pipeline_longform_reliability.py` (full file, 44 tests) × 20 | **0 failures** |
| Related suites (`test_pipeline_longform_reliability.py` + `test_pipeline_preflight_consistency.py`) | **pass** |
| Full `pytest` | **pass** (whole suite) |
| Leftover probe/worker processes after validation (CIM command-line check) | none |

History of intermediate loops (transparency): the first full-file ×20 loop
failed 2/20 on an over-strict probe in the gap test (test-only, fixed); the
first orphan-test loop passed 9/10 on a racy liveness assertion (test-only,
fixed with exit-code checks). Both loops were restarted after their fixes.

## GitHub Windows CI

| Run | Result |
| --- | --- |
| initial iteration (push 30064804234, PR 30064882656, manual rerun) | **success** — superseded by the P1 fix |
| final iteration push run (30067940112) | **success** |
| final iteration PR run (30067942071) | **success** |
| final iteration manual rerun (30067942071) | **success** |

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
real (100/100 each), the handoff-timeout path kills launcher *and* real
interpreter deterministically (100/100), no skip/xfail/relaxed assertions,
full-file 20/20, related suites and full pytest pass, no leftover processes,
locks and leases release on failure paths, child startup failures are
diagnosable, and the final Windows CI runs on the P1-fixed code (initial +
manual rerun) passed. **Issue #10 is closed by PR #12.**
