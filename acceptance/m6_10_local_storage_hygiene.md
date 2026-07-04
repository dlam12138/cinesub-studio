# M6.10 Local Storage Hygiene & Cleanup Policy

## Goal

Let the project safely identify which local artifacts can be cleaned, which data must be protected, and which large files should be archived or confirmed before removal. The cleanup tool defaults to audit and dry-run only.

## Tool

Run the audit from the project root:

```powershell
.\.venv\Scripts\python.exe -B scripts\cleanup_local_artifacts.py
```

The default command prints top-level disk usage, cleanup candidates, skipped/refused paths, and total candidate size. It does not delete files.

Deletion requires an explicit category or path plus `--apply`:

```powershell
.\.venv\Scripts\python.exe -B scripts\cleanup_local_artifacts.py --category tmp --apply
```

`--path` only narrows scope. It is not a dangerous override mode and cannot bypass protected or tracked-file rules.

## Categories

- `tmp`: `.tmp/`
- `pycache`: `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, and `pytest-cache-files-*`
- `dist`: generated `dist/cinesub-portable/`; release archives only with `--include-release-archives`
- `cache`: `.cache/pip/` and `.cache/huggingface/`
- `portable-python`: `tools/python/`
- `logs`: old generated `*.log` files only; never the whole `logs/` directory by default
- `outputs`: user data and outputs such as `output/`, `work/`, `input/`, `uploads/`, `archive/`, `failed/`, `reports/`, and `models/`

User-data deletion requires both `--apply` and `--confirm-user-data`:

```powershell
.\.venv\Scripts\python.exe -B scripts\cleanup_local_artifacts.py --category outputs --apply --confirm-user-data
```

Release archives require `--include-release-archives` before they can be selected:

```powershell
.\.venv\Scripts\python.exe -B scripts\cleanup_local_artifacts.py --category dist --include-release-archives
```

## Guardrails

The tool uses `git ls-files` to protect tracked files. If tracked-file detection fails, audit still runs, but deletion is disabled even when `--apply` is passed.

The tool never selects or deletes:

- `project_evaluation_report.md`
- tracked files
- `.git/`
- `.venv/`
- `src/`
- `web/`
- `tests/`
- `scripts/`
- `acceptance/`
- `config/`
- Provider/Profile local config
- `README.md`
- `TRIAL.md`
- `requirements*.txt`

Generated cache directories such as `__pycache__/` may be cleaned when selected by the `pycache` category, but the protected source/documentation directories themselves remain protected.

## Local Storage Hotspots Observed

The pre-M7 local audit showed the largest project-local areas were:

- `tools/`
- `archive/`
- `.git/`
- `.cache/`
- `dist/`
- `.tmp/`
- `work/`
- `models/`
- `uploads/`
- `.venv/`

`archive/`, `work/`, `models/`, `uploads/`, and `output/` can contain user data or reproducibility evidence, so they remain protected unless the user explicitly confirms user-data cleanup.

## Validation

M6.10 validation should include:

```powershell
.\.venv\Scripts\python.exe -B -m pytest
.\.venv\Scripts\python.exe -B -m pytest tests\test_text_encoding_hygiene.py
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

## Non-Goals

M6.10 does not change ASR, mixed-language detection, Web UI, Provider/Profile behavior, pipeline execution, or release-builder behavior.
