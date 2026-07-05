# M8.3 Full Routed Segment Payload And Coverage-Gated Apply

## What M8.3 Adds

M8.3 makes experimental `--segment-asr-routing apply` capable of accepting routed subtitle output when full timestamped per-window ASR segments and full routed window coverage are available.

The default remains unchanged. `off` does not run segment routing, and `dry_run` remains preview/report oriented. Only explicit `apply` can affect the final source SRT.

## Difference From M8.2

M8.2 added the active apply foundation: staged candidate SRT assembly, validation, atomic replacement, and fallback boundaries.

M8.3 adds the missing full-segment payload path. The prototype can now include full timestamped segments only when explicitly requested, and active apply rejects routed output unless coverage is complete.

## Full Segment Payload Contract

The payload contains media duration, full-coverage window planning, coverage metadata, and per-window runs. Segment timestamps are local to each window unless a payload explicitly marks `timestamp_scope: "global"`.

Generated full-segment reports may contain transcript text and must remain under ignored output/report directories. Acceptance notes and audit bundles must not include full transcript segment payloads.

## Preview-Only Evidence

Preview fields are never subtitle text. `preview`, `text_preview`, detected language, segment count, and classification reason may support analysis, but they must not be assembled into SRT cues.

If timestamped `start`, `end`, and `text` segment records are unavailable, non-strict apply falls back and strict apply fails cleanly.

## Full Coverage Gate

Coverage means routed ASR windows cover the media duration. It does not mean every second must produce subtitle text.

Full coverage planning includes a final partial window when duration is not an exact multiple of `window_seconds`, so tail gaps are not silently ignored.

If media duration is unknown or coverage cannot be calculated reliably, active apply must fallback or strict-fail. Routed output is not accepted without known duration and coverage metadata.

## needs_review And skip_window Policy

`needs_review` may select auto only when auto has full usable timestamped segments.

`skip_window` does not fabricate text and cannot be accepted by active apply in M8.3. It triggers fallback or strict failure.

No-speech windows are only distinguishable from missing data when the prototype records successful full-segment availability with an empty segment list. M8.3 remains conservative where that distinction cannot safely produce routed subtitles.

## Fallback And Strict Behavior

Non-strict apply preserves the normal baseline SRT, writes a report, and marks `fallback_used: true`.

Strict apply raises `SegmentAsrRoutingError` and reports that no routed subtitle output was accepted.

M8.3 preserves the M8.2 staged candidate safety mechanism: assemble to a candidate path, validate cue count, then atomically replace the final SRT only after acceptance.

## Experimental Limits

M8.3 does not prove semantic correctness of routed transcripts. It only makes the active apply path structurally capable of succeeding when full segments and full coverage are present.

Real media smoke tests, window parameter tuning, performance limits, and long-file risk controls remain M8.4 work.

## Verification

Recommended checks:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"

.\.venv\Scripts\python.exe -B -m pytest tests\test_segment_asr_prototype.py
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
Run the agent-control-marker grep used in previous milestones and confirm no new marker pollution was introduced.
```
