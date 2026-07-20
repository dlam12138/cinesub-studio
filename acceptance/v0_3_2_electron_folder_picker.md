# v0.3.2 Electron Native Folder Picker

## Summary

- Starting commit: `83ab6f8 v0.3.1: fix electron desktop shell startup`
- Goal: add an Electron-only native folder picker for the batch processing video directory input.
- Scope: desktop shell and Web UI progressive enhancement only.

## Desktop Folder Picker Behavior

- `desktop/main.js` registers one IPC handler: `dialog:select-directory`.
- The handler uses Electron `dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] })`.
- The handler returns only the selected directory path string, or `null` when the dialog is canceled.
- The handler does not expose filesystem APIs, process APIs, backend internals, Provider config, or secrets.

## Web UI Behavior

- The batch processing `pipelineInputDir` text input remains the source of truth for the input directory.
- A `选择文件夹` button appears beside the input only when `window.cineSubDesktop.selectDirectory` is available.
- Browser mode keeps the existing manual text input behavior and hides the native picker button by default.
- Choosing a directory fills `pipelineInputDir`; scan and run continue to call the existing pipeline API paths.

## Explicit Non-Changes

- No backend changes.
- No pipeline behavior changes.
- No ASR or translation changes.
- No Provider/Profile ownership or API changes.
- No runtime diagnostics API changes.
- No installer, packaging, code signing, auto-update, bundled Python, bundled FFmpeg, or bundled models.
- No downloader, model hub, TTS, dubbing, voice clone, lip-sync, or media output expansion.

## Tests

- Passed: `.\.venv\Scripts\python.exe -B -m pytest tests\test_electron_shell_readiness.py tests\test_premium_ui_refresh.py tests\test_electron_folder_picker.py -q`
  - Result: `29 passed`
- Passed: `.\.venv\Scripts\python.exe -B -m pytest tests\test_electron_shell_readiness.py tests\test_premium_ui_refresh.py -q`
  - Result: `22 passed`
- Passed: `.\.venv\Scripts\python.exe -B -m pytest tests -q`
  - Result: full suite passed with the existing `job_api.py` background-thread warning previously seen in v0.3.1 validation.
- Passed: `.\start_web.ps1 -Smoke -NoBrowser -NonInteractive`
  - Result: launcher/import smoke completed; no browser, model download, or media processing started.
- Passed: `git diff --check`

## Manual Verification

- Ran a bounded desktop smoke from `desktop` using `npm start`.
- Observed backend startup log: `Starting backend with ...\.venv\Scripts\python.exe`.
- Confirmed homepage served HTTP 200 on `http://127.0.0.1:7860/`.
- Confirmed the launched process tree was project-local:
  - Electron root: `desktop\node_modules\electron\dist\electron.exe .`
  - Backend command: `.venv\Scripts\python.exe -B start_app.py --no-browser --non-interactive --port 7860`
- This shell-only run did not interactively click the native folder dialog.
- Cleanup used the identified Electron PID tree; after cleanup, `http://127.0.0.1:7860/` was unreachable and no listener remained on port `7860`.
