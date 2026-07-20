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
- Backend shutdown behavior: Electron stores only its child process handle. On Windows it terminates that recorded backend PID tree with `taskkill /pid <pid> /T /F` during app quit/window close; on other platforms it calls `child.kill()`. It does not scan for or kill unrelated Python processes.
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

## v0.3.1 Electron Runtime Availability Rerun

Starting commit:

- `85fbc24 v0.3: add electron desktop shell`

Scope:

- Only Electron runtime binary availability and desktop shell rerun were checked.
- No backend, Web UI, pipeline, ASR, translation, Provider/Profile, installer, electron-builder, Python bundling, FFmpeg bundling, or model bundling work was done.

Runtime availability:

- `cd desktop`
- `npm install` succeeded with `electron@43.0.0` kept unchanged.
- Default Electron install script had previously left `node_modules/electron/dist/electron.exe` missing. Running Electron's install script with a temporary process environment succeeded:
  - `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`
  - npm proxy and HTTP(S) proxy variables cleared for that command only.
- No mirror, proxy, or cache setting was written into production code or project config.
- `desktop/node_modules/electron/dist/electron.exe --version` returned `v43.0.0`.

Manual desktop rerun:

- `npm start` opened the Electron window.
- Main window title observed: `字幕工坊 — 视频字幕生成器`.
- Homepage loaded from `http://127.0.0.1:7860/` with HTTP 200 and `CineSub Studio` present.
- Runtime diagnostics API loaded: `GET /api/runtime/diagnostics` HTTP 200.
- Recent tasks / pipeline status API loaded: `GET /api/pipeline/status` HTTP 200.
- Language style/profile API loaded: `GET /api/language-profiles` HTTP 200.
- Provider/translation settings API loaded: `GET /api/providers` HTTP 200.
- Homepage contained the expected navigation text for `运行环境`, `最近任务`, `翻译`, and `语言风格`.

Shutdown rerun:

- First rerun found a real desktop shell lifecycle bug: closing Electron stopped the direct `start_app.py` child, but the launched Web backend process tree remained alive and continued serving port 7860.
- Fix applied in `desktop/main.js`: on Windows, `stopBackend()` now terminates the recorded backend PID tree with `taskkill /pid <pid> /T /F`; it still does not scan for unrelated Python processes.
- After the fix, closing Electron made `http://127.0.0.1:7860/` unreachable and left no active listener on port 7860.

Artifact hygiene:

- `desktop/node_modules/` remained ignored and was not staged.
- Electron runtime binary, cache, `dist/`, `out/`, and release artifacts were not staged.
- Code changes were limited to the desktop shell shutdown fix and its readiness test.

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
