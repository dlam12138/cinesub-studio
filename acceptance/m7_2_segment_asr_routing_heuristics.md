# M7.2 Segment ASR Routing Heuristics

## What M7.2 Adds

M7.2 adds a CLI-only analyzer for M7.1 segment ASR prototype reports. It reads existing JSON reports, normalizes per-window ASR metadata, classifies each window into a conservative routing suggestion, and produces both Markdown and machine-readable JSON summaries.

## What It Does Not Change

M7.2 does not change production transcription, subtitle generation, Web jobs, Provider/Profile behavior, release building, or the batch pipeline. It does not call faster-whisper, load models, read audio/video files, or generate subtitles.

## Input Report Assumptions

The analyzer expects a top-level JSON object with a `windows` list. It accepts the current M7.1 shape with `windows[].results[]` entries keyed by `mode`, and the planned mapping shape with `windows[].runs.{auto,forced-fr,forced-en}`. Each window needs `start_seconds` and `end_seconds`, either directly on the window object or under a nested `window` object.

## Routing Categories

- `keep_auto`: auto ASR is usable, confident, target-language evidence without stronger contradiction.
- `prefer_forced_fr`: auto is weak and forced French has the strongest usable metadata evidence.
- `prefer_forced_en`: auto is weak and forced English has the strongest usable metadata evidence.
- `needs_review`: evidence is conflicting or ambiguous enough that automated routing would be unsafe.
- `skip_window`: no run has usable text, enough segments, and a clean error state.

## Evidence-Only Boundary

This analyzer does not prove transcript correctness. It only summarizes M7.1 ASR comparison evidence and produces conservative routing suggestions for future design. Forced-language outputs remain comparison modes, not proof that the transcript is semantically better.

## How To Run

```powershell
.\.venv\Scripts\python.exe -B src\tools\segment_asr_report_analyzer.py output\reports\asr_evidence\some_report.json `
  --output-json output\reports\asr_evidence\m7_2_routing_summary.json `
  --output-md output\reports\asr_evidence\m7_2_routing_summary.md
```

Multiple reports can be analyzed together:

```powershell
.\.venv\Scripts\python.exe -B src\tools\segment_asr_report_analyzer.py report1.json report2.json
```

Optional settings:

```text
--confidence-threshold 0.70
--min-segments 1
```

## Future M7.3 Direction

M7.3 could use accumulated M7.2 summaries to decide whether segment-level ASR routing is reliable enough to prototype behind a guarded non-production switch. Any future production routing should still keep subtitle generation behavior unchanged until the evidence shows stable wins across real mixed-language samples.
