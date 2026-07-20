# M8.4 Segment ASR Apply Smoke And Guardrails

## What M8.4 Adds

M8.4 keeps segment ASR routing experimental and opt-in, but makes `apply` safer to try on real media by adding apply-only runtime controls:

- `--segment-routing-window-seconds`
- `--segment-routing-max-windows`
- `--segment-routing-allow-large-run`

The default remains `off`. Users who do not explicitly choose `--segment-asr-routing apply` get the same transcription behavior as before.

## Difference From M8.3

M8.3 made routed apply structurally possible when full timestamped segments and full coverage were available.

M8.4 adds a preflight before full-coverage routed ASR. It estimates the number of routed windows and ASR calls, then rejects or falls back before expensive work when duration is unknown or the per-media window cap would be exceeded.

## Window Controls

`window_seconds` controls the full-coverage apply window length. The default is `120.0`.

Full-coverage planning includes the final partial tail window. For example, `duration=125` and `window_seconds=60` produces windows covering `0-60`, `60-120`, and `120-125`.

## Runtime Guardrails

Reports include additive `runtime_guardrails` metadata:

```json
{
  "window_seconds": 120.0,
  "planned_window_count": 60,
  "estimated_asr_calls": 180,
  "max_windows": 80,
  "cap_exceeded": false,
  "allow_large_run": false
}
```

`estimated_asr_calls` is `planned_window_count * 3`, covering `auto`, `forced-fr`, and `forced-en`.

## Max-Window Behavior

`max_windows` is applied per media item in pipeline/batch mode, not to the whole queue.

If planned windows exceed the cap and `allow_large_run` is false:

- non-strict apply preserves the baseline SRT and writes a fallback report
- strict apply writes a controlled failure report and raises `SegmentAsrRoutingError`

Full routed ASR is not invoked in either case.

## allow_large_run Behavior

`allow_large_run` bypasses only the max-window cap.

It does not bypass known-duration requirements, full coverage validation, preview-only rejection, candidate SRT validation, cue-count validation, or strict failure behavior.

## Fallback Vs Strict

Non-strict apply keeps the normal baseline SRT whenever preflight, coverage, segment availability, or candidate assembly fails.

Strict apply fails cleanly and reports that no routed subtitle output was accepted. If a baseline SRT already exists, it remains preserved.

## Smoke-Run Workflow

Real media and generated output are not committed. If a local sample exists and a user explicitly allows a real run, evidence should remain under ignored `work/`, `output/`, or report directories.

Single-file smoke command:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  work\sample_16k.wav `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 80
```

Pipeline smoke command:

```powershell
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 80
```

For large media, only add:

```powershell
--segment-routing-allow-large-run
```

after reviewing the planned window count and expected ASR call count.

## Default Behavior

`off` performs no routing work and writes no routing report.

`dry_run` remains report-only. M8.4 window guardrails are apply-only and do not change dry-run sampling.

Old Web submissions that do not include the new fields resolve to safe defaults: `window_seconds=120.0`, `max_windows=80`, and `allow_large_run=false`.

## Generated Outputs

Full segment payloads may contain transcript text. Acceptance notes and audit bundles must not include full transcript segment payloads, real media, generated subtitle output, models, secrets, or transient work directories.

## Verification Notes

Run targeted and full test suites, `git diff --check`, and the existing marker-pollution check. The acceptance document should describe that check in words and avoid writing literal agent-control marker tokens that would make the search match this file.

## Future M8.5 Direction

M8.5 can focus on real-material smoke evidence, clearer report presentation, failure-recovery experience, and documentation polish after M8.4 guardrails are stable.
