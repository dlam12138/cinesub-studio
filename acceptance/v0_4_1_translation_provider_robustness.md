# v0.4.1 Translation Provider Structured Output Robustness

- Starting commit: `f7a5fe5 v0.4: fix desktop preview blocker`
- Branch: `v0.3-electron-desktop-shell`
- Validation date: 2026-07-08

## Original Blocker

The v0.4 Desktop Preview freeze found that the real French sample completed ASR and generated a non-empty source SRT, but DeepSeek returned invalid or incomplete structured translation output:

- One response included `//` comments inside the JSON payload.
- Another response omitted an expected subtitle id.

The job failed before quality check, so the sample could not be considered a usable desktop preview baseline.

## Fix

The fix is limited to `src/core/subtitle_translate.py`:

- Strip `//` line comments that appear outside JSON strings before parsing model translation output.
- Retry a batch once when the provider returns malformed JSON structure or omits expected ids.
- Add stricter retry-only instructions requiring valid JSON, exact ids, no comments, no Markdown, and no context ids.
- Keep failure explicit if the retry still returns invalid or incomplete structure.
- Do not write a translated/bilingual SRT as success when structured output remains invalid.

No ASR, Electron, UI architecture, Provider/Profile ownership, installer, downloader, packaging, or model-loading behavior was changed.

## Regression Coverage

Added `tests/test_translation_structured_output.py` covering:

- DeepSeek-style JSON with `//` comments.
- Incomplete structured output that succeeds after one strict retry.
- Persistent incomplete structured output that raises a Provider structured-output error and does not create an output SRT.

## Real Sample Revalidation

Sample:

```text
tests/e2e_samples/fr_short/34584660077-1-192.mp4
```

Provider/Profile:

```text
Provider: deepseek-main
Profile: fr-film
```

Result:

- Job id: `e8a984a953ec`
- Status: `done`
- Stage: `completed`
- Return code: `0`
- ASR completed.
- Translation completed.
- Quality check completed.
- Recent jobs API showed the job as `done` / `completed`.

Observed robustness evidence from logs:

```text
Provider returned invalid structured output for batch 8/15; retrying once with stricter JSON instructions.
Provider returned invalid structured output for batch 13/15; retrying once with stricter JSON instructions.
Translation done: .\output\34584660077-1-192.large-v3.bilingual.zh-CN.srt
Quality check completed.
Finished.
```

Output paths:

```text
output/34584660077-1-192.large-v3.srt
output/34584660077-1-192.large-v3.bilingual.zh-CN.srt
output/reports/34584660077-1-192.large-v3.quality_report.json
output/reports/34584660077-1-192.large-v3.review_needed.srt
```

Output evidence:

- Source SRT: non-empty, `25422` bytes.
- Bilingual SRT: non-empty, `34119` bytes.
- Quality report: non-empty, `12482` bytes.
- `/download?job=e8a984a953ec&type=source` returned HTTP 200.
- `/download?job=e8a984a953ec&type=translated` returned HTTP 200.

Generated outputs stayed in ignored runtime directories and were not staged for commit.

## Remaining Risk

External Provider output can still be unstable. If the provider returns invalid or incomplete structured output twice for the same batch, the job now fails clearly with a Provider structured-output diagnostic instead of silently producing empty or partial subtitles.

## Tests Run

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_translation_structured_output.py tests\test_translation_prompt.py tests\test_srt_utils.py -q
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\core\subtitle_translate.py --self-test
.\.venv\Scripts\python.exe -B -m pytest tests -q
git diff --check
```

Results:

- Translation/provider focused tests: `12 passed`.
- `subtitle_translate.py --self-test`: passed.
- Full test suite: passed, with the existing non-fatal background-thread warning in `job_api.py`.
- `git diff --check`: passed.
