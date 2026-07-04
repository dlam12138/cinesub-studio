# M7.0 Mixed-Language ASR Evidence

## Purpose

M7.0 adds a low-risk evidence layer for mixed-language media. It answers whether a file appears to contain more than one spoken language, which sampled time ranges suggest each language, and whether later segment-level ASR routing is worth prototyping.

This milestone does not solve mixed-language production ASR.

## CLI Usage

Run the standalone tool:

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\tools\mixed_language_asr_evidence.py "D:\Movies\movie.mkv"
```

Defaults:

- `--model small`
- `--device auto`
- `--samples 8`
- `--sample-seconds 30`
- `--output-dir output/reports/asr_evidence`

The tool extracts temporary WAV samples under `.tmp/asr-evidence/`, runs faster-whisper language detection on each sample, and writes JSON plus Markdown reports.

## Report Directory

Reports are written to:

```text
output/reports/asr_evidence/
```

Generated report files are runtime artifacts and should not be committed.

## Web Read-Only Behavior

The Web UI exposes existing reports only:

- `GET /api/asr-evidence/reports`
- `GET /api/asr-evidence/report?file=<basename>`

The endpoints only read validated `*.asr_evidence.json` files from `output/reports/asr_evidence/`. They reject path traversal, absolute paths, and non-report files. The Web UI does not run Whisper, start background jobs, or modify pipeline state.

## Local-Only Model Policy

Model loading is local-only by default. Missing local models fail with:

```text
Model not found locally. Re-run with --allow-model-download, or pre-cache the model.
```

The tool may only allow faster-whisper downloads when the user explicitly passes `--allow-model-download`.

## Known Limitations

- This is sampled evidence only.
- It is not production ASR routing.
- It does not guarantee every spoken language was sampled.
- Production transcription still uses the existing task-level behavior.

## Validation Results

Recorded during implementation:

- `pytest`: 100 passed
- basic import check: passed
- subtitle translate self-test: passed
- quality checker self-test: passed
- Web smoke homepage: 200
- Web smoke `/api/runtime/diagnostics`: 200
- git directive marker grep on `HEAD`: no matches

## Unchanged Behavior

M7.0 does not change:

- `transcribe_to_srt()` production behavior
- pipeline run/retry behavior
- subtitle output generation
- Provider/Profile schema
- translation prompt behavior
- release builder behavior
