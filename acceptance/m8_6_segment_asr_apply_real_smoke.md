# M8.6 Segment ASR Apply Real Smoke And Performance Snapshot

## What M8.6 Adds

M8.6 prepares for an authorized real-sample smoke run by adding an offline, redacted smoke-report helper and documenting the exact smoke workflow. Because no explicit local media sample path was authorized, M8.6 does **not** run real Whisper, FFmpeg, ASR, or media processing.

New deliverables:

- `src/tools/segment_asr_smoke_report.py` — reads segment ASR routing report JSON files and writes a Markdown summary with redacted paths and no transcript text.
- `tests/test_segment_asr_smoke_report.py` — automated tests using synthetic JSON fixtures only.
- `acceptance/m8_6_segment_asr_apply_real_smoke.md` — this document.

The default segment ASR routing mode remains `off`. No production pipeline behavior changes for users who do not explicitly enable routing.

## Difference From M8.5

M8.5 added report UX summaries and documented manual smoke workflows.

M8.6 does not redesign the apply algorithm. It adds a reusable offline helper for summarizing routing reports and records the planned real-smoke procedure. If a real sample is authorized later, the same helper can convert the generated report JSON into a redacted acceptance / audit summary.

## Smoke Report Helper

`segment_asr_smoke_report.py` accepts one or more glob patterns pointing at `segment_asr_routing_integration` report JSON files and writes a Markdown document containing:

- A scenario summary table with mode, status, apply result, fallback, strict flag, coverage, window count, and estimated ASR calls.
- Per-scenario details including runtime guardrails, coverage summary, and user-facing messages.
- Redacted JSON snapshots safe for acceptance / audit bundles.

Redaction rules enforced by the helper:

- `segments` / `full_segments` / `text` fields are replaced with a count placeholder.
- Absolute media paths are converted to `~/<relative>` form or kept as project-relative paths.
- Full transcript payloads are never copied into the Markdown output.

The helper only reads existing report JSON. It does not invoke Whisper, FFmpeg, or any translation API.

## Planned Real Smoke Commands

The following commands are the planned manual smoke workflow once an explicit sample path is authorized:

Non-strict apply with conservative window cap:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  "<AUTHORIZED_SAMPLE_PATH>" `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 20
```

Strict guardrail failure smoke with deliberately low cap:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  "<AUTHORIZED_SAMPLE_PATH>" `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 1 `
  --segment-routing-strict
```

Optional dry-run comparison on the same sample:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  "<AUTHORIZED_SAMPLE_PATH>" `
  --segment-asr-routing dry_run `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 20
```

After a real run, the helper can summarize the report:

```powershell
.\.venv\Scripts\python.exe -B src\tools\segment_asr_smoke_report.py `
  output\reports\segment_asr_routing\*.json `
  --output-md output\reports\segment_asr_routing\m8_6_smoke_summary.md
```

## Smoke Evidence To Capture

For each executed scenario, the planned evidence capture includes:

- Command shape with sample path redacted
- Exit code
- Media duration if available
- Wall-clock runtime
- Segment routing mode
- `apply_attempted`, `apply_succeeded`, `fallback_used`
- Fallback reason if any
- `subtitle_output_affected`
- Window seconds, planned window count, estimated ASR calls, max windows
- Cap exceeded, allow large run
- Coverage full, coverage rate, gap count
- Candidate accepted, preview-only rejected
- Report path and final SRT path existence
- Whether baseline SRT was preserved on fallback / strict failure

Full transcript segment payloads, generated SRT content, and raw audio/video files must not be committed or included in acceptance / audit bundles.

## Smoke Execution Status

Real media smoke was documented only, not executed in M8.6.

```text
Real media smoke was not executed because no explicit authorized sample path was provided.
```

A later real local sample run requires explicit user authorization of a specific path, e.g.:

```text
授权样本：work\xxx_16k.wav
```

Generated smoke outputs are not committed because media, SRT output, reports with possible transcript evidence, and runtime work files belong under ignored runtime directories.

## What Remains Experimental

Segment routing `apply` remains opt-in and experimental. `dry_run` remains the safer evidence-gathering path. `apply` still requires full coverage, usable full segments, candidate SRT validation, and runtime guardrails before routed output can affect the final SRT.

## Future Direction

M8.7 can focus on an authorized real-sample regression run using the workflow documented here. If M8.7 results are acceptable, M8.8 or M9 can move toward performance tuning, window presets, beta UX, and broader media regression before considering a non-experimental default.

## Verification Notes

Run targeted and full tests, `git diff --check`, base imports, and the previous marker-pollution grep. Keep generated logs and audit material UTF-8 encoded, and exclude media, model files, secrets, runtime outputs, and full transcript payloads from audit bundles.
