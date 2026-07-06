# M8.8 Runtime Environment Fix And Smoke Rerun

## Summary

M8.7 authorized real smoke was executed, but all three smoke scenarios failed before reaching routing logic due to an `_overlapped` / Windows Socket Provider initialization error inside the `faster_whisper -> ctranslate2 -> asyncio` import chain.

M8.8 goal was to diagnose and fix the runtime environment, prove the ASR import chain works, and re-run a controlled smoke only after the import chain is healthy.

Results:

- The original `.venv` was based on **system Python 3.13.3** and its import chain was transiently healthy at M8.8 start.
- After user confirmation, `.venv` was **rebuilt with portable Python 3.12.10** (`tools/python/python.exe`).
- All import probes passed in the new portable-Python `.venv`.
- All three smoke scenarios were re-run on a user-authorized short clip in the new `.venv` and **passed**.
- No routing code changes were made.

---

## M8.7 Failure Recap

Observed failure in M8.7:

```text
faster_whisper -> ctranslate2 -> asyncio.windows_events -> _overlapped
OSError: [WinError 10106] 无法加载或初始化请求的服务提供程序。
```

This blocked all smoke scenarios before reaching segment ASR routing logic.

---

## Runtime Diagnosis Results

### Git precondition

- Commit: `af92af9 Document M8.7 authorized smoke environment block`
- Tag: `m8.7-authorized-smoke-env-blocked`
- `git status`: only historical audit zips and `project_evaluation_report.md` untracked
- `git diff --check`: no whitespace errors

### Python runtime (initial diagnosis — system Python)

- Executable: `.venv/Scripts/python.exe` (created from system Python)
- Version: `3.13.3` (tags/v3.13.3:6280bb5, Apr 8 2025, 14:47:33) [MSC v.1943 64 bit (AMD64)]
- Platform: `Windows-11-10.0.26100-SP0`
- `pyvenv.cfg` home: `<system_python_313_home>`

### Python runtime (after rebuild — portable Python)

- Executable: `.venv/Scripts/python.exe` (created from `tools/python/python.exe`)
- Version: `3.12.10` (tags/v3.12.10:0cc8128, Apr 8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]
- Platform: `Windows-11-10.0.26100-SP0`
- Portable Python location: `tools/python/python.exe`

### Package versions (system Python 3.13 — initial diagnosis)

| Package | Version |
|---------|---------|
| faster-whisper | 1.2.1 |
| ctranslate2 | 4.8.0 |
| numpy | 2.5.0 |
| av | 17.1.0 |

### Package versions (portable Python 3.12 — after rebuild)

| Package | Version |
|---------|---------|
| faster-whisper | 1.2.1 |
| ctranslate2 | 4.8.1 |
| numpy | 2.5.1 |
| av | 18.0.0 |
| onnxruntime | 1.27.0 |

### Import probes (portable Python 3.12)

| Import | Result |
|--------|--------|
| `_overlapped` | ok |
| `asyncio.windows_events` | ok |
| `ctranslate2` | ok (4.8.1) |
| `faster_whisper` | ok (1.2.1) |
| Project baseline (`transcribe`, `subtitle_translate`, `quality_checker`, `batch_worker`, `web_server`, `runtime_env`, `runtime_paths`, `subtitle_model`, `runtime_api`, `pipeline_api`) | ok |

### Fix strategy used

**Phase A — Initial diagnosis (system Python 3.13)**:

- All import probes passed without any package upgrades.
- Hypothesis: the M8.7 `_overlapped` failure was a transient Windows Socket Provider initialization issue (possibly a process-state or service-availability race condition) that resolved by the time M8.8 began.

**Phase B — Rebuild with portable Python 3.12 (user-authorized)**:

- After user review, `.venv` was rebuilt with `tools/python/python.exe` (portable Python 3.12.10).
- Steps:
  1. `mv .venv .venv-backup-system-py313`
  2. `tools/python/python.exe -m venv .venv --clear`
  3. `.venv/Scripts/python.exe -m ensurepip --default-pip`
  4. `.venv/Scripts/python.exe -m pip install --upgrade pip`
  5. `.venv/Scripts/python.exe -m pip install -r requirements.txt`
  6. `.venv/Scripts/python.exe -m pip install pytest anyio`
- All import probes and smoke scenarios re-passed in the new `.venv`.
- The backup `.venv-backup-system-py313` is retained locally but not committed.

---

## Derived Clip Authorization

The authorized source (`<authorized_93min_16k_wav_source>`, ~93 minutes) is covered by M8.7 authorization.

A 3-minute derived clip was authorized by the user and created:

- Source: `work/<authorized_93min_16k_wav_source>`
- Clip: `work/m8_8_authorized_smoke_clip_16k.wav`
- Parameters: `-ss 00:05:00 -t 00:03:00 -ac 1 -ar 16000`
- The clip is not committed.

---

## Smoke Scenario Results (portable Python 3.12)

All scenarios used `--device cpu --model small --local-files-only` in the rebuilt portable-Python `.venv`.

### Scenario 1: Non-strict apply

Command:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  work\m8_8_authorized_smoke_clip_16k.wav `
  --device cpu --model small --local-files-only `
  --output-dir work\pytest-artifacts\m8_8_smoke_py312 `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 20
```

Result:

- Detected language: `fr (0.97)`
- `Segment ASR routing: applied routed SRT successfully.`
- Routing report generated.
- Exit code: 0
- Routing logic was reached and succeeded.

### Scenario 2: Strict guardrail failure

Command:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  work\m8_8_authorized_smoke_clip_16k.wav `
  --device cpu --model small --local-files-only `
  --output-dir work\pytest-artifacts\m8_8_smoke_py312 `
  --segment-asr-routing apply `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 1 `
  --segment-routing-strict
```

Result:

- Detected language: `fr (0.97)`
- `ERROR: Segment ASR routing: failed in strict mode. Reason: segment routing apply window count 2 exceeds max 1.`
- Exit code: non-zero (routing failure)
- Guardrail correctly triggered; no routed SRT accepted.

### Scenario 3: Dry-run comparison

Command:

```powershell
.\.venv\Scripts\python.exe -B src\core\transcribe.py `
  work\m8_8_authorized_smoke_clip_16k.wav `
  --device cpu --model small --local-files-only `
  --output-dir work\pytest-artifacts\m8_8_smoke_py312 `
  --segment-asr-routing dry_run `
  --segment-routing-window-seconds 120 `
  --segment-routing-max-windows 20
```

Result:

- Detected language: `fr (0.97)`
- `Segment ASR routing: dry_run completed; subtitle output was not changed.`
- Routing report generated.
- Exit code: 0
- Dry-run did not affect final SRT.

---

## Test Results

Targeted tests run after smoke in portable-Python `.venv`:

| Test file | Result |
|-----------|--------|
| `tests/test_segment_asr_smoke_report.py` | 15 passed |
| `tests/test_segment_asr_routing_integration.py` | 27 passed |
| `tests/test_segment_asr_routing_runtime_guards.py` | 9 passed |
| `tests/test_segment_asr_routing_report_ux.py` | 7 passed |
| **Total** | **58 passed** |

Full suite was not required because no source code or dependency files were changed.

---

## Source Changes

No source code changes were made in M8.8.

Files touched:

- `acceptance/m8_8_runtime_env_fix_and_smoke_rerun.md` (new)
- `.gitignore` (add exception for this acceptance doc)

Files not touched (as mandated):

- `src/tools/segment_asr_routing_integration.py`
- `src/tools/segment_asr_srt_assembler.py`
- `src/core/transcribe.py`
- `src/pipeline/batch_worker.py`
- `src/web/*`
- `web/index.html`

---

## Why Generated Outputs Are Not Committed

- `work/m8_8_authorized_smoke_clip_16k.wav` — derived media clip, excluded by `.gitignore` (`*.wav`)
- SRT files — generated artifacts, excluded by `.gitignore` (`*.srt`)
- Routing report JSON — contains segment-level metadata, excluded by `.gitignore`
- Audit bundle `audit/external_audit_m8_8.zip` — historical artifact, excluded by convention
- `.venv-backup-system-py313` — backup venv, excluded by `.gitignore` (`.venv/`)

Only the acceptance document and `.gitignore` exception are intended for version control.

---

## What Remains Experimental

1. **Segment ASR routing on full-length files**: The 3-minute clip validated the mechanism; a ~93-minute full file has not been run with routing apply in this milestone.
2. **Language detection on mixed-language content**: The clip was French-dominant (`fr 0.97`). Mixed-language scenarios remain unvalidated with real audio.
3. **CUDA path**: All smoke used `--device cpu`. The CUDA import chain was not exercised in this milestone.
4. **Pipeline integration**: `batch_worker.py` segment routing integration was not re-smoked with real audio in this milestone.

---

## Future Direction

### M8.9 candidates

1. **Full-file routing apply smoke**: Run segment ASR routing apply on the authorized 93-minute source (requires separate runtime cost authorization).
2. **Pipeline end-to-end smoke**: Validate `batch_worker.py` segment routing with real audio through the full pipeline.
3. **CUDA environment validation**: Re-verify CUDA path after the import chain fix, to ensure GPU inference still works.

### M9 direction

If M8.9 validates full-file and pipeline integration, M9 can move to broader release readiness: packaging, documentation, and user-facing Web UI polish for segment routing controls.

---

## Evidence Checklist

- [x] M8.7 failure recap documented
- [x] Runtime diagnosis results recorded (both system Python and portable Python)
- [x] Python / ctranslate2 / faster-whisper versions documented (before and after rebuild)
- [x] Fix strategy documented (Phase A: transient diagnosis; Phase B: portable Python rebuild)
- [x] Import probes pass (portable Python 3.12)
- [x] Short derived clip authorized and created
- [x] Real smoke rerun completed (3 scenarios, portable Python 3.12)
- [x] Smoke scenario results recorded
- [x] No remaining blockers
- [x] Generated outputs exclusion explained
- [x] Experimental items listed
- [x] Future direction proposed
- [x] Tests pass (58 passed)
- [x] Git diff check clean
- [x] External audit bundle generated
