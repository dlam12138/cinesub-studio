# M8.2 Segment ASR Routing Active Apply

## What M8.2 Adds

M8.2 turns `--segment-asr-routing apply` from a deferred placeholder into an experimental active apply path. The feature remains opt-in and does not change default transcription behavior.

The new foundation is a narrow routed SRT assembler that accepts real timestamped per-window ASR segments, converts them into global SRT cues, writes UTF-8 output atomically, and returns assembly metadata for audit reports.

## Difference From M8.1

M8.1 accepted `apply` as a public shape but did not attempt routed subtitle generation. Non-strict `apply` wrote a deferred fallback report, while strict `apply` failed early.

M8.2 attempts active routed SRT generation. On success, the final source SRT can be replaced by routed output and reports mark `subtitle_output_affected: true`. On failure, non-strict mode falls back to the normal baseline SRT and strict mode fails cleanly.

## Active Apply Flow

The apply path runs the existing segment ASR prototype and analyzer, then asks a narrow full-segment helper for real timestamped routed segments. The helper must return full `start`, `end`, and `text` segment data, or report that full segments are unavailable.

Current live M7.1 evidence persists preview strings and segment counts, not full subtitle segments. Therefore live M8.2 apply is fallback-limited until full segment output support is added. Tests use mocked full segments to prove the active apply and assembly path.

## Fallback And Strict Behavior

For non-strict `apply`, failures keep the normal SRT baseline and write a routing report with `fallback_used: true`, `apply_attempted: true`, and `apply_succeeded: false`.

For strict `apply`, failures raise a controlled `SegmentAsrRoutingError`. If a normal baseline SRT already exists, it is only a baseline artifact; the error and report state that routed apply failed and no routed subtitle output was accepted.

## Default Behavior

Default behavior remains unchanged. When routing mode is `off`, no routing report is written and no segment ASR work runs. Existing single-file, Web, pipeline, retry, completed skip, Provider, Language Profile, and diagnostics behavior remains unchanged except for additive routing metadata when routing is explicitly enabled.

## SRT Assembly

`segment_asr_srt_assembler.py` accepts routed windows with selected runs and timestamped segments. It interprets segment timestamps as local to each window unless `timestamp_scope: "global"` is set, sorts by global start time, drops empty or invalid segments, renumbers cues from 1, conservatively adjusts simple overlaps, and writes UTF-8 SRT through a temporary file followed by atomic replacement.

Assembly metadata includes cue count, dropped segment count, overlap adjustment count, skipped window count, and selected run counts.

## Preview-Only Evidence

Preview strings are never used as subtitle text. If only preview evidence is available, apply reports `full_routed_segments_available: false`, `preview_only_rejected: true`, and falls back or fails strictly according to the selected mode.

## Experimental Limits

M8.2 does not claim semantic correctness for routing decisions. `needs_review` may only use auto when full usable auto segments are available. `skip_window` never fabricates text; it is omitted or causes fallback/strict failure depending on the available routed payload.

## Verification

Recommended checks:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"

.\.venv\Scripts\python.exe -B -m pytest tests\test_segment_asr_srt_assembler.py
.\.venv\Scripts\python.exe -B -m pytest tests\test_segment_asr_routing_integration.py

.\.venv\Scripts\python.exe -B -m pytest `
  tests\test_segment_asr_report_analyzer.py `
  tests\test_segment_asr_report_analyzer_golden.py `
  tests\test_segment_asr_routing_sandbox.py `
  tests\test_segment_asr_routing_policy_dry_run.py `
  tests\test_segment_asr_routing_integration.py

.\.venv\Scripts\python.exe -B -m pytest
git diff --check
git status --short --untracked-files=all
git grep "::git-" HEAD
```

## Future M8.3 Direction

M8.3 should add a real full-segment provider for routed windows, backed by timestamped ASR output rather than preview reports. Once that provider exists, active apply can move from fallback-limited live behavior toward real per-window routed subtitle generation.
