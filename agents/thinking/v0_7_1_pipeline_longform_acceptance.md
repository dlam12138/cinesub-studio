# v0.7.1 Pipeline Longform Acceptance Rerun

## User Goal

Use one local MKV as anonymous `sample-long-02` to rerun Stage 2 scenarios
S1, S2, S3, and especially S7, then update and commit only the approved
anonymous acceptance reports. Do not modify runtime code, run the ASR
campaign, bump the version, build release archives, or create tags.

## Facts And Evidence

- Git branch start was `6f87b90`; runtime base was `252f75c`.
- The single selected MKV was copied to the ignored private area and the copy
  matched the source SHA256. Public metadata is limited to a 45.1 minute MKV
  and source hash prefix `f296414a2968`.
- `large-v3` was available locally and ran with CUDA/float16 and
  `local_files_only=True`.
- Two S1 plan calls returned 200 with stable classification and no observed
  filesystem, state, run-record, output, archive, or lease side effects.
- During S7, only the Web listener was terminated. The same worker PID and
  creation FILETIME remained alive, held the worker lease, completed ASR in
  225.46 seconds, and completed 27 deterministic local translation requests.
- The restarted Web recognized the same live run and rejected a second run as
  busy.
- Quality checking then failed with `OSError: [Errno 22] Invalid argument`.
  The task state became failed, finalization and archive did not run, and the
  run record remained running after the worker left the process list.
- All acceptance helper, Web, worker, monitor, and stub processes were removed
  after evidence capture. The original provider configuration was restored.

## Decisions

- Classify Stage 2 as `Fail - release blocker` under the strict S7 rules.
- Stop before the requested S3 live collision rerun and before Stage 3 or
  Release Prep.
- Do not modify production code in this acceptance-only task.
- Keep all media, subtitles, full hashes, complete fingerprints, logs, run
  records, and process evidence in ignored private storage.
- Use `tests/test_pipeline_offline_e2e.py` for the stage-reuse matrix because
  the requested `tests/test_pipeline_stage_reuse_acceptance.py` is absent on
  this branch.

## Operations

- Verified Git preconditions; the initial fetch failed once because the
  network connection reset, while branch and local SHA checks succeeded.
- Probed, hashed, copied, and ignore-checked the anonymous sample.
- Started a deterministic local translation stub and an independent Web
  process, executed two S1 plans, started S2, killed only the Web listener
  during ASR, restarted Web, and monitored the original worker.
- Corrected only ignored private acceptance helper scripts while diagnosing
  the run; no runtime source was changed.
- Updated the three public acceptance reports and this thinking record.

## Validation

- Targeted longform, preflight, offline stage-reuse, and privacy suites:
  61 passed.
- Full pytest: 516 passed.
- Translation and quality self-tests: passed with the repository `PYTHONPATH`.
- Web smoke, Electron JavaScript syntax checks, and `git diff --check`: passed.
- Privacy audit found no real source path, filename, complete source hash,
  transcript, prompt, secret, or token in the public reports.
- The evidence content commit is `90eaccb`.

## Unresolved And Next

- The live worker's inherited process I/O must remain usable after the Web
  listener is force-terminated.
- Worker exit must always produce a terminal run record; exited Windows
  process objects must not be treated as live solely from matching creation
  FILETIME.
- After fixes, rerun S1, S2, S3, and S7 with a fresh isolated private input.
- Do not begin Stage 3, Release Prep, versioning, packaging, or tagging before
  that rerun passes.

## Evidence Cleanup

- Review confirmed that this branch records a failed acceptance result rather
  than a Stage 2 pass.
- Removed the superseded pre-rerun `Conditional Pass` progress note and two
  push-only operation notes.
- Revalidated that the Markdown, JSON, and CSV reports agree on S1 pass,
  S2 fail, S3 not run, S7 fail, and a release blocker.
- JSON and CSV parsing, `git diff --check`, allowlist inspection, and the
  public privacy scan passed with no forbidden-data hits.
- Created the cleanup commit and attempted to push it twice; both attempts
  failed at the GitHub network boundary (connection reset, then port 443
  timeout). No repeated push loop was started.
- No production code, release artifact, version, tag, Stage 3 work, or Release
  Prep work was changed by this cleanup.
