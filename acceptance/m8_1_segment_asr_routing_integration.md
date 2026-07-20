# M8.1 Segment ASR Routing Opt-In Integration

## What M8.1 Adds

M8.1 adds an experimental segment ASR routing control surface to single-file processing and the batch pipeline. The supported modes are `off`, `dry_run`, and `apply`.

The integration is intentionally narrow: it connects the existing M7 prototype and analyzer to production entrypoints without replacing the current subtitle generation path.

## Default Behavior

Default behavior is unchanged. When routing is `off`, the application does not run segment routing, does not generate routing reports, and does not add extra ASR work.

Existing output paths, retry behavior, completed-task skip checks, Provider handling, Language Profile behavior, and Web job lifecycle remain unchanged except for option parsing and pass-through.

## Single-File Opt-In

Single-file routing can be enabled through the CLI:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py D:\Movies\movie.mkv --segment-asr-routing dry_run
```

The Web single-file form exposes the same controls in a compact experimental advanced section. Existing form submissions without these fields resolve to `off`.

## Pipeline Opt-In

The batch worker accepts the same CLI controls:

```powershell
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --segment-asr-routing dry_run
```

The Web pipeline form passes the same settings only when the user explicitly chooses non-default routing settings.

## Mode Semantics

- `off`: current ASR path only; no routing report is generated.
- `dry_run`: current ASR path still generates subtitles; routing evidence and analyzer metadata are generated separately.
- `apply`: accepted for forward compatibility, but active routed subtitle generation is deferred to M8.2.

## Fallback Behavior

For `dry_run`, failures fall back to the normal ASR result unless strict mode is enabled. The fallback is recorded in the integration report when a report can be written.

For non-strict `apply`, M8.1 writes a deferred/fallback report and keeps `subtitle_output_affected: false`.

For strict `apply`, M8.1 fails cleanly before reporting normal subtitle output as successful. It does not generate partial routed subtitles.

## Reports

Routing reports are written under:

```text
output/reports/segment_asr_routing/
```

Reports include:

- `segment_asr_routing_mode`
- `subtitle_output_affected`
- `fallback_used`
- `fallback_reason`
- `experimental`
- `confidence_threshold`
- `min_segments`
- window planning settings
- prototype report paths when available
- analyzer summary and per-window classifications when available

For M8.1 `dry_run`, conservative M7.1 prototype sampling defaults are used and recorded. M8.1 does not add broad new window-planning controls.

## Experimental Boundaries

M8.1 does not implement active per-window ASR replacement. It does not attempt to merge routed windows into a production SRT, and it does not mark subtitle output as affected.

The feature remains experimental and opt-in. It is safe to leave disabled.

## Intentionally Not Changed

M8.1 does not change:

- existing `transcribe_to_srt()` output semantics
- completed-task skip validation
- failed-task retry selection
- output artifact naming
- Provider or Language Profile storage
- diagnostics stable fields
- release builder behavior

## Future M8.2 Direction

M8.2 should design and test production-safe active apply behavior: per-window ASR choice, deterministic SRT assembly, timing reconciliation, fallback boundaries, and quality gates before enabling `subtitle_output_affected: true`.
