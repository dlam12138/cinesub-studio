# 智译字幕工坊 / CineSub Studio Desktop Shell

This directory contains the v0.3 Electron desktop shell for 智译字幕工坊 / CineSub Studio.

## What It Does

- Starts the existing Python Web launcher from the repository root.
- Waits until the local Web server is ready.
- Opens the existing 智译字幕工坊 / CineSub Studio Web UI in an Electron window.
- Stops the child Python backend process when the Electron app exits.

The Python Web app remains the source of truth. Electron does not duplicate backend APIs.

## What It Does Not Do

- No installer.
- No release package.
- No code signing.
- No auto-update.
- No bundled Python or `.venv`.
- No bundled FFmpeg.
- No bundled models.
- No model downloader.
- No FFmpeg downloader.
- No ASR, translation, pipeline, Provider, or Language Profile behavior changes.

## Setup

From the repository root, make sure the Python development environment already works:

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
```

Then install desktop dependencies:

```powershell
cd desktop
npm install
```

## Start

```powershell
cd desktop
npm start
```

By default the shell uses port `7860`. To choose a different port:

```powershell
$env:CINESUB_DESKTOP_PORT = "7861"
npm start
```

## Backend Launch

The desktop shell starts:

```text
python -B start_app.py --no-browser --non-interactive --port <port>
```

Python resolution order:

1. Project `.venv/Scripts/python.exe` on Windows.
2. Project `.venv/bin/python` on Unix-like systems.
3. `python` from `PATH`.
4. Windows `py -3` launcher.

## Known Limitations

- Python and the project `.venv` are still required.
- FFmpeg is not bundled.
- Models are not bundled.
- There is no installer yet.
- There is no code signing.
- There is no auto-update.
- This is not an official release package.
