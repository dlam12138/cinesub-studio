# v0.1 Internal Preview Validation

## Starting Point

- Validation date: 2026-07-07 17:37:45 +08:00
- Starting commit: `72f0b2f m14: prepare windows portable release`
- Goal: freeze after M14 and validate one real Web workflow without adding features or doing release packaging.

## Sample And Configuration

- Sample exists: yes
- Sample path: `tests/e2e_samples/fr_short/34584660077-1-192.mp4`
- Sample size: 71,179,659 bytes
- Media: H.264 video, AAC audio, 1280x718, duration `908.875238` seconds
- Provider: `deepseek-main`
- Provider key: configured, recorded only as masked `sk-...60cb`
- Translation model: `deepseek-v4-flash`
- Language Profile: `fr-film`
- Web job settings: `large-v3`, `cuda`, `float16`, forced source language `fr`, SRT only, translation enabled, bilingual output requested

## Validation Results

- Base import check: passed, `imports ok`
- Web launcher: started with `.\start_web.ps1 -NoBrowser -NonInteractive -Port 7860`
- Homepage: HTTP 200 at `http://127.0.0.1:7860/`
- Runtime diagnostics API: HTTP 200 at `/api/runtime/diagnostics`
- Runtime diagnostics summary: `ok`, message `当前运行环境检查通过。`
- Submitted Web job: `e599dc5d488c`
- Recent jobs/history API: showed the submitted failed job with status `failed`
- Output subtitles: not generated
- Output/download inspection: not verifiable because the job failed before ASR completed

## Blocker Classification

- Result: failed before subtitle output
- Classification: environment/external model download blocker, not a code blocker
- Failure point: model loading for `large-v3`
- Error summary: `requests.exceptions.ChunkedEncodingError: Connection broken: IncompleteRead(...)`
- Relevant job log summary:
  - audio extraction started successfully into `work/`
  - model load began with `Device: cuda, compute_type: float16`
  - `Local files only: False`
  - Hugging Face model download for `Systran/faster-whisper-large-v3` was interrupted
  - process exited with return code `1`

No code change was made for this failure because it is a model download/network availability issue. Per freeze policy, environment and external service blockers are recorded only.

## Notes

- Provider configuration was not created or edited.
- Pipeline and Web job code were not changed.
- Generated runtime files, logs, model/cache files, work audio, screenshots, subtitles, and audit artifacts remain untracked/ignored and are not part of this validation commit.
- User-facing friction observed: first real run depends on completing the large Whisper model download; when the download is interrupted, the job fails before producing any subtitle output.

## Rerun After Model Availability

- Rerun date: 2026-07-07 18:03:43 +08:00
- Baseline commit: `d652cc7 v0.1: validate internal preview workflow`
- Sample path: `tests/e2e_samples/fr_short/34584660077-1-192.mp4`
- Model source: `Systran/faster-whisper-large-v3`
- Model target: project-local `models/`
- Expected model size: about 3 GB
- Model availability result: unblocked
- Availability verification: `large-v3` loaded successfully with `local_files_only=True`
- Completed model file observed: `models/models--Systran--faster-whisper-large-v3/snapshots/edaa852ec7e145841d8ffdb056a99866b5f0a478/model.bin`, 3,087,284,237 bytes

## Rerun Results

- Base import check: passed, `imports ok`
- Web launcher: started with `.\start_web.ps1 -NoBrowser -NonInteractive -Port 7860`
- Homepage: HTTP 200 at `http://127.0.0.1:7860/`
- Runtime diagnostics API: HTTP 200 at `/api/runtime/diagnostics`
- Runtime diagnostics summary: `ok`, message `当前运行环境检查通过。`
- Submitted Web job: `0a55d70f699c`
- Job result: `done`, return code `0`
- ASR result: detected language `fr` with probability `1.00`
- Translation result: completed 291 subtitle entries in 15 batches using `deepseek-v4-flash`
- Source SRT: `output/34584660077-1-192.large-v3.srt`, 25,421 bytes
- Bilingual SRT: `output/34584660077-1-192.large-v3.bilingual.zh-CN.srt`, 34,772 bytes
- Recent jobs/history API: showed the completed job with source and translated output paths
- Download links:
  - `/download?job=0a55d70f699c&type=source`: HTTP 200, 25,421 bytes
  - `/download?job=0a55d70f699c&type=translated`: HTTP 200, 34,772 bytes

## Rerun Notes

- The original model blocker was resolved by completing the project-local model cache. No model files or cache files are committed.
- Provider configuration, pipeline code, Web job code, UI, and release logic were not changed.
- Non-blocking issue observed: the job log reported `Quality check failed: name 'detected' is not defined` after subtitle generation. The job still completed and both SRT outputs were available, so this was not treated as a v0.1 freeze blocker.
- v0.1 internal preview evidence: the current app can launch, diagnose runtime, submit a real Web job, run ASR, call translation, generate non-empty SRT outputs, show the job in history, and serve output downloads.
