# v0.4 Desktop Preview Freeze

- Product: 智译字幕工坊 / CineSub Studio v0.4 Desktop Preview
- Starting commit: `f67266e v0.3.3: rename product brand`
- Branch: `v0.3-electron-desktop-shell`
- Validation date: 2026-07-08

## Summary

This freeze validated the current Electron desktop preview baseline without adding product features. One desktop startup blocker was found and fixed: Electron crashed when loading the app directly from the Chinese project path. The fix stages the Electron shell files into a temporary ASCII path at `npm start`, while keeping the backend rooted at the real project directory through `CINESUB_REPO_ROOT`.

No ASR, translation, pipeline, Provider/Profile ownership, ASR precedence, downloader, packaging, installer, bundled Python, bundled FFmpeg, or bundled model behavior was changed.

## Validation Results

- Clean baseline: starting commit confirmed as `f67266e`; `git diff --check` passed before edits.
- Electron startup: passed after blocker fix. `cd desktop; npm start` opens Electron and starts the Python backend automatically.
- Web UI in Electron: passed. Homepage returned HTTP 200 and the Electron window title showed `智译字幕工坊 — AI 字幕生成器`.
- Backend startup: passed. Backend served `http://127.0.0.1:7860/` and `/api/runtime/diagnostics`.
- Backend shutdown: passed. Closing the Electron main window with a normal close signal stopped Electron and made port 7860 unreachable.
- Runtime diagnostics: passed. `diagnostic_summary.status` was `ok`; `ffmpeg_source` was `bundled`; CUDA, model cache, Provider, and project directories were reported usable.
- Provider settings: passed. `/api/providers` returned `deepseek-main` with masked key only.
- Language Profile settings: passed. `/api/language-profiles` returned built-in profiles including `fr-film`.
- Recent jobs/history: passed. `/api/jobs` returned the submitted sample jobs and their final statuses.
- Folder picker: readiness passed by Electron IPC/static validation. `desktop/main.js` registers `dialog:select-directory`, `desktop/preload.js` exposes only `selectDirectory`, and the Web UI fills `pipelineInputDir` from the selected path. The native OS dialog was not visually driven by automation during this run.
- Download links: passed for generated source and translated output paths; source and translated download endpoints returned HTTP 200 for the sample job.

## Real Sample Workflow

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

- Job `23187a5d327c`: ASR completed, source SRT generated, translation failed at batch 12/20 because the model returned JSON containing `//` comments.
- Job `15a6f38e670c`: ASR completed, source SRT generated, translation failed at batch 22/39 because the model response omitted subtitle id `220`.
- Because the failures were caused by LLM output not satisfying the existing strict translation parser, no parser or translation behavior change was made during this freeze.
- Quality check did not complete because both sample jobs ended with translation return code `1`.

Generated output paths observed:

```text
output/34584660077-1-192.large-v3.srt
output/34584660077-1-192.large-v3.bilingual.zh-CN.srt
output/34584660077-1-192.large-v3.lang.json
```

Output file evidence:

- Source SRT existed and was non-empty: `29197` bytes.
- Bilingual/translated SRT path existed and was non-empty: `34657` bytes.
- `/download?job=15a6f38e670c&type=source` returned HTTP 200.
- `/download?job=15a6f38e670c&type=translated` returned HTTP 200.

Generated outputs were left under ignored runtime directories and were not staged for commit.

## Blockers Found And Fixed

Fixed blocker:

- Electron 43.0.0 crashed with `EXCEPTION_ACCESS_VIOLATION` before the backend started when loading the app from the Chinese project path.
- Electron 37.10.3 also crashed when loading the app directly from the Chinese project path.
- A minimal Electron app and the same shell staged under an ASCII temp path worked.
- Fix: lock Electron to `37.10.3`, add `desktop/launch.js`, change `npm start` to `node launch.js`, stage `main.js` and `preload.js` into `%TEMP%\cinesub-studio-electron-app`, and pass the real repo root through `CINESUB_REPO_ROOT`.

Unfixed freeze limitation:

- The real sample did not reach completed status because the remote LLM returned invalid or incomplete structured output. This is documented as a preview limitation; translation parsing/algorithm behavior was intentionally not changed.

## Known Limitations

- Python and the project `.venv` are still required.
- Electron runtime must be installed through `npm install`.
- FFmpeg is not bundled by the Electron shell.
- Models are not bundled.
- There is no installer yet.
- There is no code signing.
- There is no auto-update.
- This is not an official public release.
- The desktop preview is validated as an internal baseline, not a release artifact.
- Remote LLM output can still cause translation jobs to fail strict JSON validation.

## Tests Run

```powershell
git status --short --untracked-files=all
git log --oneline -5
git diff --check
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
.\.venv\Scripts\python.exe -B -m pytest tests\test_branding_text.py tests\test_electron_shell_readiness.py tests\test_electron_folder_picker.py tests\test_premium_ui_refresh.py -q
.\.venv\Scripts\python.exe -B -m pytest tests -q
cd desktop
npm start
```

Focused tests after the desktop startup blocker fix:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_branding_text.py tests\test_electron_shell_readiness.py tests\test_electron_folder_picker.py tests\test_premium_ui_refresh.py -q
```

Observed results:

- Backend smoke: passed.
- Focused tests before fix: `32 passed`.
- Full test suite before fix: passed with one existing thread warning in `tests/test_web_runtime_diagnostics_ui.py`.
- Focused tests after fix: `33 passed`.
- Backend smoke, full test suite, and `git diff --check` after fix: passed. The full suite still emitted the same non-fatal background thread warning in `job_api.py`.
