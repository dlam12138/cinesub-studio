# M7.3 Segment ASR Routing Sandbox — Golden Fixtures & Schema Baseline

## What M7.3 Adds

M7.3 adds a **stable golden fixture layer** and **schema documentation** on top of M7.1 and M7.2. It does not introduce new production features; it hardens the evidence layer so that future heuristic changes are auditable and regression-testable.

### New Files

```text
tests/fixtures/asr_evidence/           # 13 deterministic M7.1 report fixtures
├── README.md                          # Input/output schema documentation
├── all_error.json
├── auto_only_usable.json
├── confidence_threshold_edge.json
├── empty_preview.json
├── en_auto_confident.json
├── fr_auto_confident.json
├── fr_auto_opp_contradiction.json
├── low_conf_auto_strong_en.json
├── low_conf_auto_strong_fr.json
├── low_conf_both_strong_conflict.json
├── mapping_shape.json
├── min_segments_edge.json
├── multi_window.json

tests/test_segment_asr_report_analyzer_golden.py  # 20 regression tests
```

### What Each Fixture Tests

| Fixture | Scenario | Expected Classification |
| ------- | -------- | ----------------------- |
| `en_auto_confident.json` | Auto English, high confidence, no conflict | `keep_auto` |
| `fr_auto_confident.json` | Auto French, high confidence, no conflict | `keep_auto` |
| `fr_auto_opp_contradiction.json` | Auto French, but forced-en is also strong | `needs_review` |
| `low_conf_auto_strong_fr.json` | Auto weak, forced-fr is strongest | `prefer_forced_fr` |
| `low_conf_auto_strong_en.json` | Auto weak, forced-en is strongest | `prefer_forced_en` |
| `low_conf_both_strong_conflict.json` | Auto weak, both forced strong and conflicting | `needs_review` |
| `all_error.json` | Every run errored | `skip_window` |
| `mapping_shape.json` | Uses `runs{}` mapping instead of `results[]` | `keep_auto` |
| `auto_only_usable.json` | Auto usable but not confident; forced runs errored | `needs_review` |
| `confidence_threshold_edge.json` | Probability sits just below threshold (0.69 < 0.70) | `prefer_forced_fr` |
| `min_segments_edge.json` | Segment count is zero, below min_segments | `skip_window` |
| `empty_preview.json` | Preview text is empty, so runs are not usable | `skip_window` |
| `multi_window.json` | Three windows with mixed classifications | Mixed |

## What It Does Not Change

M7.3 does **not** change:

- `transcribe_to_srt()` or any production ASR behavior
- Web job routing or subtitle generation
- The batch pipeline
- Provider / Profile configuration
- Release builder
- Any existing M7.1 or M7.2 code

All production paths remain untouched.

## Schema Baseline

### M7.1 Report Input Schema (v1)

- Top-level object with `windows` list
- Each window has `start_seconds`, `end_seconds`, and per-run ASR data
- Two equivalent shapes accepted: `results[]` and `runs{}`
- Per-run fields: `detected_language`, `detected_language_probability` / `language_probability`, `segment_count`, `text_preview` / `preview`, `error`

See `tests/fixtures/asr_evidence/README.md` for full field table and normalization rules.

### M7.2 Analyzer Output Schema (v1)

- Top-level: `schema_version`, `input_files`, `settings`, `summary`, `windows`
- `settings`: `confidence_threshold`, `min_segments`
- `summary`: `total_windows`, plus counts for each of the 5 classifications
- `windows[]`: `source_file`, `window_index`, `start_seconds`, `end_seconds`, `classification`, `reason`, `auto`, `forced_fr`, `forced_en`
- Normalized run summary: `detected_language`, `detected_language_probability`, `segment_count`, `has_error`, `usable`

Schema version policy: future changes must increment `schema_version` and update acceptance + regression tests.

## Regression Test Coverage

`tests/test_segment_asr_report_analyzer_golden.py` adds 20 tests:

- **12 golden fixture routing tests** — each fixture must produce its expected classification
- **1 multi-window aggregate test** — verifies per-window counts across a 3-window fixture
- **2 settings-sensitivity tests** — changing `confidence_threshold` shifts routing as expected
- **1 schema-invariant test** — verifies all documented output fields are present
- **1 schema-version test** — `schema_version` must be `1`
- **1 multi-file aggregation test** — analyzing multiple fixtures together produces correct totals
- **1 reason-text test** — every classification carries a non-empty reason
- **1 CLI end-to-end test** — CLI processes a fixture and writes JSON + Markdown without models

All tests are deterministic, offline, and run in under 1 second.

## Test Results

```text
tests/test_segment_asr_report_analyzer_golden.py  20 passed
tests/test_segment_asr_report_analyzer.py        9  passed
tests/test_segment_asr_prototype.py              10 passed
tests/test_mixed_language_asr_evidence.py       8  passed
--------------------------------------------------------------------------------
Full pytest suite                                119 passed
```

`git diff --check` is clean.

## How to Run

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -m pytest tests\test_segment_asr_report_analyzer_golden.py -v
```

To run the full regression suite:

```powershell
.\.venv\Scripts\python.exe -B -m pytest
```

## Future Direction (M7.4)

M7.4 could extend the fixture layer by:

- Adding real M7.1 report snapshots as fixtures (from actual mixed-language audio samples)
- Introducing `tests/fixtures/routing_summaries/` with expected M7.2 output for each fixture
- Adding a schema-validation helper that rejects unknown fields in strict mode
- Running a sandbox sweep across parameter ranges (`confidence_threshold` from 0.50 to 0.95) and documenting where classification boundaries flip

Any future integration into production routing should still wait until:

1. Golden fixtures cover real-world mixed-language samples
2. Sandbox parameter sweeps show stable wins across multiple samples
3. Acceptance documents explicitly document the integration boundary

## Limitations

- Fixtures are **synthetic**, not real ASR outputs. They test heuristic behavior, not model accuracy.
- The analyzer does **not** prove transcript correctness. It only summarizes ASR metadata evidence.
- No production routing is performed. `keep_auto`, `prefer_forced_fr`, etc. are suggestions, not automated actions.
