# Thinking — Windows Pipeline Lock / Lease / Handoff Stability (Issue #10)

## Recorded context

* base_sha: `2c317d256a18c0ad6ed8ded4534b481fc3b59234`
* Windows version: Windows 11, 10.0.26100
* Python version: 3.12.10 (project `.venv`)
* pytest version: 9.1.1

## Did this change production code? If yes, why were test fixes not enough?

Yes — one minimal, evidence-based hardening in `src/web/pipeline_api.py`
(`run_pipeline_background`): the exception path now records
`process.poll()` when the worker already exited, so a worker that crashes
before the handoff ack yields a deterministic terminal `returncode` instead
of `None`.

Why a pure test fix was not enough:

* The regression coverage required by the stability spec (§10.4) explicitly
  demands that every background-worker outcome — exit 0, non-zero exit,
  startup failure, fast exit before first poll — ends in an observable
  terminal code, never a permanent `None`.
* With the old code, the startup-failure case provably leaves
  `returncode=None` (verified by reading the exception path: the local
  `returncode` was never assigned before the `RuntimeError`), which the API
  surface cannot distinguish from "still running". That is a real (if minor)
  observability defect, not a test artifact.
* The change only copies an exit code that the process object already knows;
  it cannot change success behavior.

Everything else (the three flaky tests) was fixed purely on the test side.

## Why this is a test bug at the root

Evidence chain:

1. 30/30 baseline failures in a shell whose `PYTHONPATH` does not contain the
   repo `src/*` dirs, with the exact known assertions
   (`'' == 'locked'`, `'' == 'leased'`, `None == 0`).
2. Running the tests' literal child command manually shows
   `ModuleNotFoundError: No module named 'pipeline_reliability'` on stderr
   and exit 1 — the empty stdout was an import crash, not a lock failure.
3. With CI-equivalent `PYTHONPATH` the unmodified tests pass 30/30.
4. CI's workflow sets `PYTHONPATH` job-wide; production workers get an
   explicit `PYTHONPATH` from `build_child_process_env`. Only the tests'
   ad-hoc children depended on the calling terminal's environment — which
   explains "flaky locally, green in CI".
5. pytest's `pythonpath` ini option patches `sys.path` inside the pytest
   process only; it never propagates to children.

## Why the runtime lock mechanism was NOT the root cause (probes, not guesses)

* Per-handle semantics probe on this Windows build: parent holds offset 0 +
  offset 1 with two handles; releasing (LK_UNLCK + close) the offset-1 handle
  leaves offset 0 HELD for other processes. The production handoff order
  (release worker lease, wait for ack, release gate) therefore has no gap
  from handle-close semantics on Windows 11 10.0.26100.
* Same-process re-lock probe: `acquire()` correctly returns False for an
  offset the same process already holds — in-process launcher probes in the
  new gap test are valid.
* Abrupt-exit probe: a child killed with `os._exit` while holding the lock
  frees it immediately for the parent (OS release on process termination).
* Handle inheritance audit: every `Popen` in `src/` uses the default
  `close_fds=True`; no `pass_fds`/`close_fds=False` anywhere, so the
  `set_handle_inheritable` marking inside `PipelineRunLock.acquire()` is
  currently inert (dead behavior, intentionally left untouched).

## How I proved Pipeline behavior was not expanded

* The only production diff is 7 lines on the exception path of
  `run_pipeline_background` (initialize `process = None`; after the existing
  error recording, capture `process.poll()` into the local `returncode`).
* Lock acquisition/release order, the handoff sequence, run-record schema,
  `get_pipeline_task` semantics and all plan/run/retry preflight logic are
  byte-identical.
* The full existing suite (including the preflight-consistency tests from
  PR #8/#9) passes unchanged.

## Post-fix stress caught one test-side probe bug (not a runtime gap)

The first full-file ×20 loop failed 2/20 in `test_lease_handoff_has_no_observable_gap`
("launch gate was free before the worker ack"). Diagnosis: the phased probe
asserted "gate held until the *test* observes the ack", while the architecture
only promises "a launcher can never hold gate + lease at once". The server
releases the gate a few ms after the ack file appears; the worker lease is
already held at that instant — the handoff was safe, the test's observation
lag fabricated the "gap". The probe now uses launcher semantics (offset 0 →
offset 1, all-or-nothing) and fails only on a real architectural violation.
After the fix: gap test 15/15, full file 20/20 (previous counts discarded per
the no-reuse rule). This is exactly why the spec requires full-file loops in
addition to per-test 100x runs.

## Why no skip / xfail was needed

* The failures were deterministic given the environment; fixing child
  bootstrap (self-locating import paths + explicit `PYTHONPATH` in the probe
  env) removed the dependency on the calling terminal entirely. The tests
  now pass in both with-`PYTHONPATH` and without-`PYTHONPATH` shells.
* Remaining races were removed structurally: explicit JSON event handshakes
  with monotonic deadlines replace fixed sleeps; the background test waits
  for a terminal state with bounded polling instead of asserting after a
  synchronous call; a dedicated regression test probes the handoff window
  for launcher-visible gaps.

## Environment subtlety worth remembering

The project `.venv` is created with `tools/python/python.exe -m venv`. On
Windows (Python 3.11+), `.venv/Scripts/python.exe` is a launcher that spawns
the real interpreter and forwards its exit code, so `Popen.pid` (launcher)
differs from the interpreter pid reported by children, and killing the
launcher too early masks the real exit code and can briefly orphan the
interpreter. The probe helper's cleanup is staged accordingly (natural exit
→ stdin EOF → terminate → kill), and all child modes are bounded so no
lock-holding orphan can outlive a test.
