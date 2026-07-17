# 智译字幕工坊 / CineSub Studio Desktop Shell

This directory contains the Electron desktop shell for 智译字幕工坊 / CineSub Studio.

## What It Does

- Starts the existing Python Web launcher from the repository root (dev mode)
  or from the bundled backend (packaged mode).
- Waits until the local Web server is ready.
- Opens the existing 智译字幕工坊 / CineSub Studio Web UI in an Electron window.
- Stops the child Python backend process when the Electron app exits.

The Python Web app remains the source of truth. Electron does not duplicate backend APIs.

## Dev Mode vs Packaged Mode

**Dev mode** (current repo checkout):
- Uses `.venv/Scripts/python.exe` or system Python.
- Launches `start_app.py` from the repo root.
- Python and the project `.venv` are still required for dev mode.
- Used for development and debugging.

**Packaged mode** (v0.6.1 external-test installer):
- Detected via `app.isPackaged`.
- Uses the complete portable Python runtime at `resources/app/python/python.exe`.
- Launches bundled `backend/start_app.py`.
- Bundled FFmpeg is prepended to PATH; GPU builds also prepend the bundled CUDA runtime DLL directory.
- Models, output, work, logs, caches, and uploads are written below `%LOCALAPPDATA%\CineSubStudio`.
- Provider and Language Profile overrides are written below `%APPDATA%\CineSubStudio\config`.

## Setup (Dev Mode)

From the repository root, make sure the Python development environment already works:

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
```

Then install desktop dependencies:

```powershell
cd desktop
npm install
```

## Start (Dev Mode)

```powershell
cd desktop
npm start
```

By default the shell uses port `7860`. To choose a different port:

```powershell
$env:CINESUB_DESKTOP_PORT = "7861"
npm start
```

## Packaging (Windows Installer Preview)

Prerequisites:
- Complete portable Python 3.12 under `tools/python/`
- Project `.venv` with all dependencies installed; its `site-packages` is copied into the staged portable runtime
- `tools/ffmpeg/bin/ffmpeg.exe` present
- `tools/cuda/` is required for the explicit GPU flavor; the legacy `-RequireCuda` switch remains supported

Build through the runtime-validating wrapper:

```powershell
.\packaging\windows\build_installer.ps1 -Flavor cpu -OnlyUnpacked
.\packaging\windows\build_installer.ps1 -Flavor gpu -OnlyUnpacked
```

Build the NSIS installer:

```powershell
.\packaging\windows\build_installer.ps1 -Flavor cpu
.\packaging\windows\build_installer.ps1 -Flavor gpu
```

Default outputs are isolated under `desktop/release/cpu/` and `desktop/release/gpu/`.
Each output contains `release_manifest.json` with artifact size and SHA-256.
Use `-OutputDir <path>` to override the default. Direct `npm run pack:win`
and `npm run dist:win` calls assume `packaging/windows/runtime/` has already been prepared.

## Backend Launch

The desktop shell starts:

```text
python -B start_app.py --no-browser --non-interactive --port <port>
```

Python resolution order (dev mode):

1. Project `.venv/Scripts/python.exe` on Windows.
2. Project `.venv/bin/python` on Unix-like systems.
3. `python` from `PATH`.
4. Windows `py -3` launcher.

## Known Limitations

- Python and the project `.venv` are still required for dev mode.
- Packaged mode requires the installer to be built; it does not exist in a plain source checkout.
- There is no code signing.
- There is no auto-update.
- The GPU flavor bundles CUDA runtime DLLs but never the NVIDIA display driver.
- Neither flavor bundles Whisper models or silently downloads them.
- This is not an official release package.
