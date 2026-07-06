# M8.5 Segment ASR Apply Smoke UX

## What M8.5 Adds

M8.5 keeps segment ASR routing experimental and opt-in, while making `apply` easier to audit and safer to try manually:

- human-readable report summaries
- concise CLI status messages
- additive Web and pipeline routing status fields
- documented manual smoke workflows

The default remains `off`. Users who do not explicitly enable segment routing get the same normal ASR behavior and no routing report.

## Difference From M8.4

M8.4 added apply runtime guardrails such as window length, maximum windows, and large-run opt-in.

M8.5 does not redesign the apply algorithm. It makes existing outcomes clearer: applied, fallback, strict failure, dry run, and off.

## Report UX Additions

Segment routing reports now include three derived views:

- `user_summary`: short status, title, message, and next action
- `decision_summary`: mode, apply attempt result, subtitle-output impact, and fallback reason
- `safety_summary`: duration, coverage, candidate acceptance, preview-only rejection, and guardrail cap state

These summaries are derived from existing machine-readable fields. They are not the source of truth for routing decisions.

Reports must not include full transcript payloads for acceptance or audit purposes.

## CLI Status Messages

When segment routing is enabled, CLI output prints concise user-facing status lines such as:

- routed SRT applied successfully
- fallback to normal ASR with a reason
- strict-mode failure with a reason
- dry-run completion with a report path

Default `off` mode remains quiet and adds no routing output.

## Web Help Text

The existing experimental controls now warn that apply is experimental, may fall back unless strict mode is enabled, and blocks large runs unless large-run opt-in is enabled.

The UI only renders additive routing fields when job or task data already contains them. Web polling, job lifecycle, diagnostics, Provider, and Language Profile behavior are unchanged.

## Pipeline Additive State

Pipeline task state can record:

- `segment_asr_routing_status`
- `segment_asr_routing_report`
- `segment_asr_routing_message`

These fields are additive. `segment_asr_routing_message` does not overwrite task error fields or failure reasons.

Completed-skip and retry-failed behavior remain unchanged.

## Manual Smoke Workflow

Single-file apply smoke:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  work\sample_16k.wav `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 20
```

Record manually:

- routing mode
- whether apply succeeded
- fallback reason, if any
- routing report path
- whether final SRT was affected
- runtime guardrail summary

Strict failure smoke:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  work\sample_16k.wav `
  --segment-asr-routing apply `
  --segment-routing-max-windows 1 `
  --segment-routing-strict
```

Expected manually:

- clean failure
- baseline SRT preserved if already generated
- no routed SRT accepted
- report or stderr explains the cap failure

Pipeline smoke:

```powershell
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 20
```

## Smoke Execution Status

Real media smoke was documented only, not executed in M8.5. A later real local sample run requires explicit user authorization.

Generated smoke outputs are not committed because media, SRT output, reports with possible transcript evidence, and runtime work files belong under ignored runtime directories.

## What Remains Experimental

Segment routing `apply` remains opt-in and experimental. `dry_run` remains the safer evidence-gathering path. `apply` still requires full coverage, usable full segments, candidate SRT validation, and runtime guardrails before routed output can affect the final SRT.

## Future Direction

M8.6 or M9 can focus on authorized real-sample regression, performance tuning, better review tooling, and deciding whether segment routing should move from experimental toward beta.

## Verification Notes

Run targeted and full tests, `git diff --check`, base imports, and the previous marker-pollution grep. Keep generated logs and audit material UTF-8 encoded, and exclude media, model files, secrets, runtime outputs, and full transcript payloads from audit bundles.
