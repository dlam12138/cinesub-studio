# M7.5 Segment ASR Routing Policy Dry-Run And Readiness Gate

## What M7.5 Adds

M7.5 introduces a deterministic, CLI-only **readiness gate** tool that consumes the evidence produced by M7.1 (segment-level ASR comparison prototype), M7.2 (offline report analyzer), M7.3 (golden fixtures and schema baseline), and M7.4 (routing sandbox replay with parameter sweep).

It does **not** add new ASR logic, new heuristics, or new routing rules. It only asks:

> Given the existing evidence and conservative thresholds, is segment-level ASR routing ready to justify a **future production integration design**?

The tool produces:

- A JSON readiness report with structured evidence, blockers, and review windows.
- A Markdown readiness report for human audit.
- One of three readiness statuses: `insufficient_evidence`, `not_ready`, or `candidate_ready_for_design`.

## Why It Remains Evidence-Only

M7.5 is explicitly **not** production integration. It is a gate that sits **after** evidence collection and **before** any design proposal for production routing.

Key constraints enforced by M7.5:

- It does not call Whisper, FFmpeg, LLM APIs, GPU, or network.
- It does not modify `transcribe_to_srt()`, production ASR behavior, pipeline behavior, Web job behavior, or subtitle generation.
- It does not modify Provider or Language Profile behavior.
- It does not claim transcript correctness.
- It does not recommend automatic production integration.

## How It Builds on M7.1–M7.4

| Milestone | Role in M7.5 |
|-----------|--------------|
| M7.1 | Produces the raw segment ASR comparison reports that M7.2 analyzes. |
| M7.2 | Classification logic (`analyze_reports`, `classify_window`) is reused directly by M7.5. |
| M7.3 | Golden fixtures provide deterministic, reproducible inputs for dry-run validation. |
| M7.4 | Sandbox JSON outputs can be fed directly into M7.5 as an optional input source. M7.5 also checks for unstable routing decisions revealed by parameter sweep. |

## Readiness Status Meanings

### `insufficient_evidence`

Use when:

- Total window count is below `--min-total-windows`.
- Input has no usable windows.
- Required evidence fields are missing or malformed.

This means: **collect more evidence before any design work**.

### `not_ready`

Use when:

- `needs_review` rate exceeds `--max-needs-review-rate`.
- `skip_window` rate exceeds `--max-skip-window-rate`.
- Parameter sweep (from M7.4 JSON) shows unstable routing decisions.
- Other blockers are present.

This means: **the evidence is structured but does not meet conservative gates**.

### `candidate_ready_for_design`

Use **only** when:

- Total windows meet `--min-total-windows`.
- `needs_review` rate is within gate.
- `skip_window` rate is within gate.
- No malformed input or major evidence gaps are found.
- No unstable routing decisions from sweep.

This means: **the evidence is structured enough to justify designing a future production integration proposal. It does not enable or validate production routing.**

**Important:** M7.5 never outputs `production_ready`.

## Readiness Gate Settings

| Flag | Default | Meaning |
|------|---------|---------|
| `--confidence-threshold` | `0.70` | Passed to M7.2 analyzer for `keep_auto` confidence. |
| `--min-segments` | `1` | Passed to M7.2 analyzer for usable run threshold. |
| `--min-total-windows` | `5` | Minimum total windows for evaluation. |
| `--max-needs-review-rate` | `0.25` | Maximum acceptable `needs_review` / total_windows. |
| `--max-skip-window-rate` | `0.10` | Maximum acceptable `skip_window` / total_windows. |

## CLI Examples

### Direct report replay

```powershell
\.venv\Scripts\python.exe -B src\tools\segment_asr_routing_policy_dry_run.py `
  tests\fixtures\asr_evidence\*.json `
  --confidence-threshold 0.70 `
  --min-segments 1 `
  --min-total-windows 5 `
  --max-needs-review-rate 0.25 `
  --max-skip-window-rate 0.10 `
  --output-json output\reports\asr_evidence\m7_5_policy_dry_run.json `
  --output-md output\reports\asr_evidence\m7_5_policy_dry_run.md
```

### Reading an existing M7.4 sandbox JSON

```powershell
\.venv\Scripts\python.exe -B src\tools\segment_asr_routing_policy_dry_run.py `
  --sandbox-json output\reports\asr_evidence\m7_4_sandbox_sweep.json `
  --min-total-windows 5 `
  --max-needs-review-rate 0.25 `
  --max-skip-window-rate 0.10
```

When `--sandbox-json` is provided and the original input files are still present, M7.5 re-analyzes them to produce full per-window review lists. If the original files are missing, it falls back to summary-level evaluation from the sandbox JSON.

### Default behavior

- Prints Markdown to stdout.
- Writes JSON only when `--output-json` is provided.
- Writes Markdown only when `--output-md` is provided.
- Returns exit code `0` on success.
- Returns exit code `1` for malformed input, missing files, invalid gate values, or analyzer/sandbox input errors.
- Prints clean stderr without traceback for normal user errors.

## Output JSON Schema Summary

```json
{
  "schema_version": 1,
  "tool": "segment_asr_routing_policy_dry_run",
  "input_files": ["..."],
  "settings": {
    "confidence_threshold": 0.7,
    "min_segments": 1,
    "min_total_windows": 5,
    "max_needs_review_rate": 0.25,
    "max_skip_window_rate": 0.10
  },
  "readiness": {
    "status": "candidate_ready_for_design",
    "reason": "...",
    "blockers": []
  },
  "summary": {
    "total_windows": 5,
    "keep_auto": 1,
    "prefer_forced_fr": 1,
    "prefer_forced_en": 1,
    "needs_review": 1,
    "skip_window": 1,
    "needs_review_rate": 0.2,
    "skip_window_rate": 0.2
  },
  "review_windows": [
    {
      "source_file": "...",
      "window_index": 3,
      "classification": "needs_review",
      "reason": "..."
    }
  ],
  "notes": [
    "This dry-run does not prove transcript correctness.",
    "This dry-run does not change production routing.",
    "candidate_ready_for_design is not production_ready."
  ]
}
```

## What Was Intentionally Not Changed

- `transcribe_to_srt()` behavior.
- Production ASR behavior.
- Pipeline behavior.
- Web job behavior.
- Subtitle generation.
- Provider/Profile behavior.
- M7.1 prototype behavior.
- M7.2 analyzer classification semantics.
- M7.3 golden fixture expected classifications.
- M7.4 sandbox replay behavior.

## Why This Is Still Not Production Routing

M7.5 does not:

- Change production ASR.
- Change `transcribe_to_srt()`.
- Change Web jobs.
- Change pipeline behavior.
- Change subtitle generation.
- Call Whisper, FFmpeg, or LLM APIs.
- Implement Provider / Language Profile boundary cleanup.
- Enable routing automatically.

`candidate_ready_for_design` is a gate for **design work**, not a go-live signal.

## Future Direction (M7.6 / M8)

M7.5 stops at evidence packaging and readiness gating. A future milestone could:

- Draft a production integration design acceptance (M7.6 or M8).
- Define how `segment_asr_routing_policy_dry_run` results feed into `transcribe_to_srt()` or the Web pipeline.
- Define staged rollout, feature flags, or A/B testing for segment-level routing.
- Address Provider / Language Profile boundary cleanup if still needed.

M7.5 provides the evidence base that makes any of those next steps possible.
