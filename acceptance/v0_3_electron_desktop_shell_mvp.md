# v0.3 Electron Desktop Shell MVP

## Summary

- Starting commit: `bc81129 v0.2: refine premium UI navigation`
- Branch: `v0.3-electron-desktop-shell`
- Goal: add a minimal Electron shell that starts the existing Python Web backend, waits for local readiness, opens the existing Web UI, and stops the child backend on app exit.

## Desktop Files Added

- `desktop/package.json`
- `desktop/package-lock.json`
- `desktop/main.js`
- `desktop/preload.js`
- `desktop/README.md`

## Runtime Behavior

- Backend launch command: `python -B start_app.py --no-browser --non-interactive --port <port>`
- Python resolution behavior:
  1. Project `.venv/Scripts/python.exe`
  2. Project `.venv/bin/python`
  3. `python` from `PATH`
  4. Windows `py -3` launcher
- Port behavior: default `7860`, with `CINESUB_DESKTOP_PORT` override. If the port already returns the CineSub homepage, Electron treats it as occupied and does not reuse it. If another process occupies the port, Electron shows a port occupied error. No silent fallback port is used.
- Server readiness behavior: Electron polls `http://127.0.0.1:<port>/` for HTTP 200 for up to 30 seconds before loading the window.
- Window behavior: `1440x900`, minimum `1100x720`, title `CineSub Studio`, `contextIsolation: true`, `nodeIntegration: false`, and local Web UI loaded from `127.0.0.1`.
- Backend shutdown behavior: Electron stores only its child process handle and calls `child.kill()` during app quit/window close. It does not kill unrelated Python processes.
- Electron resolved version: `43.0.0` in `desktop/package-lock.json`.

## Manual Electron smoke result

Attempted:

- `cd desktop`
- `npm install`
- `npm start`

Result:

- `npm install` completed and produced `desktop/package-lock.json`.
- `npm start` did not reach the Electron window because Electron's runtime binary download failed locally with `TypeError: fetch failed` and `Electron failed to install correctly`.
- Static Electron shell tests, backend smoke, import smoke, UI regressions, quality regression, and full pytest passed.
- No pipeline, Web UI, ASR, translation, Provider/Profile, launcher semantics, or runtime downloader behavior was changed to work around the local Electron binary download failure.
- No Electron or project backend process remained after the failed manual attempt.
- `desktop/node_modules/`, `desktop/dist/`, `desktop/out/`, `.vite`, and desktop log outputs are ignored and were not staged.

## Tests Run

- `.\.venv\Scripts\python.exe -B -m pytest tests\test_electron_shell_readiness.py -q` passed: 12 passed.
- `.\.venv\Scripts\python.exe -B -m pytest tests\test_premium_ui_refresh.py tests\test_web_ui_productization.py tests\test_web_queue_history_progress_ui.py tests\test_web_runtime_diagnostics_ui.py tests\test_web_settings_center.py -q` passed: 60 passed, with an existing background-thread warning from `job_api.py`.
- `.\.venv\Scripts\python.exe -B -m pytest tests\test_quality_checker_boilerplate.py -q` passed: 4 passed.
- Import smoke from `AGENTS.md` passed: `imports ok`.
- `.\start_web.ps1 -Smoke -NoBrowser -NonInteractive` passed.
- `.\.venv\Scripts\python.exe -B -m pytest tests -q` passed, with the same existing background-thread warning from `job_api.py`.
- `git diff --check` passed.

## Explicit Non-Goals

- No installer
- No Electron/Tauri packaging
- No bundled Python
- No bundled `.venv`
- No bundled FFmpeg
- No bundled models
- No auto-update
- No code signing
- No model downloader
- No FFmpeg downloader
- No TTS/dubbing
- No pipeline changes
- No ASR or translation behavior changes
- No Provider/Profile ownership changes
