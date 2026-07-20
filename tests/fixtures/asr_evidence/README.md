# M7.3 Golden Fixture Schema

This directory contains **deterministic JSON fixtures** for regression-testing the
M7.2 segment ASR report analyzer. No model, no GPU, no audio, no network.

## Directory Layout

```text
tests/fixtures/
├── asr_evidence/          # M7.1 report input fixtures (what the analyzer reads)
│   ├── README.md          # this file
│   └── *.json             # per-scenario M7.1 report JSON
└── routing_summaries/     # (reserved) M7.2 expected output fixtures if needed
```

## M7.1 Report Input Schema

The analyzer accepts a **top-level JSON object** with the following required shape.
Only fields relevant to the analyzer are listed; `metadata` is opaque to the analyzer.

### Top-level object

| Field          | Type   | Required | Description                          |
| -------------- | ------ | -------- | ------------------------------------ |
| `schema_version` | int    | no       | Should be `1`.                       |
| `report_type`    | string | no       | `"segment_asr_prototype"`.            |
| `windows`        | list   | **yes**  | List of per-window comparison data.  |

### `windows[]` item

Each window item may contain:

| Field            | Type   | Required | Description                                   |
| ---------------- | ------ | -------- | --------------------------------------------- |
| `window_index`   | int    | no       | Window number; falls back to array position.  |
| `start_seconds`  | float  | **yes**  | Window start time in seconds.                |
| `end_seconds`    | float  | **yes**  | Window end time in seconds.                  |

Time fields may also appear under a nested `window` object:

```json
{
  "window": {
    "start_seconds": 0.0,
    "end_seconds": 60.0
  },
  ...
}
```

### Per-window ASR runs

The analyzer accepts **two equivalent shapes**:

#### Shape A: `results[]` (M7.1 default)

```json
{
  "results": [
    { "mode": "auto", ... },
    { "mode": "forced-fr", ... },
    { "mode": "forced-en", ... }
  ]
}
```

#### Shape B: `runs{}` mapping (planned)

```json
{
  "runs": {
    "auto": { ... },
    "forced-fr": { ... },
    "forced-en": { ... }
  }
}
```

### Per-run fields (both shapes)

| Field                        | Type    | Required | Description                              |
| ---------------------------- | ------- | -------- | ---------------------------------------- |
| `detected_language`          | string  | no       | Detected language code, e.g. `"fr"`.    |
| `detected_language_probability` | float | no       | Language confidence (0.0–1.0).          |
| `language_probability`       | float   | no       | Alias for `detected_language_probability`.|
| `segment_count`              | int     | no       | Number of transcribed segments.          |
| `text_preview`               | string  | no       | Short preview of transcript text.        |
| `preview`                    | string  | no       | Alias for `text_preview`.                |
| `error`                      | string  | no       | Non-empty if the run failed.             |

**Normalization rules**

- `detected_language` is lowercased.
- `detected_language_probability` and `language_probability` are tried in that order; rounded to 4 decimals.
- `segment_count` defaults to `0` if missing or invalid.
- `text_preview` and `preview` are tried in that order; defaults to `""`.
- `error` defaults to `""`; non-empty means `has_error = true`.
- A run is `usable` iff `has_error` is false, `segment_count >= min_segments`, and `preview` is non-empty.

## M7.2 Analyzer Output Schema

The analyzer produces a JSON summary with the following stable fields.

### Top-level object

| Field           | Type   | Description                                      |
| --------------- | ------ | ------------------------------------------------ |
| `schema_version`| int    | `1` for M7.3 baseline.                          |
| `input_files`   | list   | Resolved absolute paths of analyzed reports.   |
| `settings`      | dict   | Analyzer settings used for this run.             |
| `summary`       | dict   | Aggregate counts per classification.             |
| `windows`       | list   | Per-window routing decisions.                    |

### `settings`

| Field                  | Type  | Default | Description                              |
| ---------------------- | ----- | ------- | ---------------------------------------- |
| `confidence_threshold` | float | 0.70    | Minimum auto probability for `keep_auto`.|
| `min_segments`         | int   | 1       | Minimum segment count for a usable run. |

### `summary`

| Field              | Type | Description                          |
| ------------------ | ---- | ------------------------------------ |
| `total_windows`    | int  | Total number of windows analyzed.   |
| `keep_auto`        | int  | Count of `keep_auto` windows.       |
| `prefer_forced_fr` | int  | Count of `prefer_forced_fr` windows.|
| `prefer_forced_en` | int  | Count of `prefer_forced_en` windows.|
| `needs_review`     | int  | Count of `needs_review` windows.    |
| `skip_window`      | int  | Count of `skip_window` windows.     |

### `windows[]` item

| Field           | Type    | Description                                      |
| --------------- | ------- | ------------------------------------------------ |
| `source_file`   | string  | Path to the M7.1 report this window came from.  |
| `window_index`  | int     | Window index.                                    |
| `start_seconds` | float   | Window start time.                               |
| `end_seconds`   | float   | Window end time.                                 |
| `classification`| string  | One of the 5 CLASSIFICATIONS values.               |
| `reason`        | string  | Human-readable routing reason.                   |
| `auto`          | dict    | Normalized summary of the auto run.              |
| `forced_fr`     | dict    | Normalized summary of the forced-fr run.       |
| `forced_en`     | dict    | Normalized summary of the forced-en run.       |

### Normalized run summary (`auto`, `forced_fr`, `forced_en`)

| Field                           | Type    | Description                        |
| ------------------------------- | ------- | ---------------------------------- |
| `detected_language`             | string  | Lowercased detected language.     |
| `detected_language_probability` | float\|null | Language probability or null.  |
| `segment_count`                 | int     | Segment count.                     |
| `has_error`                     | bool    | True if the run had an error.      |
| `usable`                        | bool    | True if the run passed usability.  |

## Schema Version Policy

- `schema_version: 1` is the M7.3 baseline.
- Future schema changes must increment the version and update:
  - this README
  - `test_schema_invariants` in `test_segment_asr_report_analyzer_golden.py`
  - `acceptance/m7_3_segment_asr_routing_sandbox_summary.md`
- The analyzer must reject reports with incompatible top-level structure, but
  should be tolerant of optional field absence within the documented schema.

## Fixture Naming Convention

Fixture names encode the scenario they test:

```text
<auto_state>[_<forced_state>][_conflict].json
```

Examples:
- `en_auto_confident.json` — auto confident English, no conflict
- `low_conf_auto_strong_fr.json` — auto weak, forced-fr strong
- `low_conf_both_strong_conflict.json` — auto weak, both forced strong and conflicting
- `all_error.json` — every run errored
- `mapping_shape.json` — uses `runs{}` mapping instead of `results[]`
- `confidence_threshold_edge.json` — probability sits near threshold boundary
- `min_segments_edge.json` — segment count sits near `min_segments` boundary
- `empty_preview.json` — no preview text, so runs are not usable
- `multi_window.json` — multiple windows with mixed classifications

## Golden Expectations

The `GOLDEN_EXPECTATIONS` dict in `test_segment_asr_report_analyzer_golden.py`
maps each fixture to its expected classification. These are the **regression
baseline**: any code change that shifts an expectation without explicit
acceptance-document justification is a breaking change.

## Limitations

These fixtures are **synthetic evidence**, not real ASR outputs. They do not
prove transcript semantic correctness. They are designed to test routing
heuristic behavior, not model accuracy.
