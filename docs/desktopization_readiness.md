# M13 Desktopization Readiness

M13 keeps 智译字幕工坊 / CineSub Studio as a local Web service with a lightweight launcher. It does not add Electron, Tauri, an installer, code signing, auto-update, or release artifacts.

## Current Local Startup

Recommended source checkout startup:

```powershell
.\start_web.ps1
```

Useful validation modes:

```powershell
.\start_web.ps1 -NoBrowser
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
.\start_web.ps1 -NoBrowser -NonInteractive
```

The default local URL is:

```text
http://127.0.0.1:7860/
```

The launcher prints startup phases, the local URL, the Python executable, and the server log path. `-Smoke` is a non-interactive readiness check: it does not open a browser, start media processing, run ASR, load models, translate, or download models.

## FFmpeg Missing Behavior

FFmpeg is required for media jobs that extract audio, but missing FFmpeg does not need to block the Web UI, settings pages, or runtime diagnostics.

When FFmpeg is missing, the launcher reports:

- accepted environment variables: `CINESUB_FFMPEG`, `FFMPEG_PATH`
- expected project location: `tools/ffmpeg/bin/`
- optional helper: `.\scripts\download_ffmpeg.ps1`
- next step: open the Web Runtime tab for diagnostics

Actual transcription or pipeline jobs remain responsible for failing clearly when FFmpeg is still unavailable.

## Future Portable Layout

Proposed future portable structure:

```text
CineSubStudio/
  start_web.ps1
  start_app.py
  src/
  web/
  config/
  tools/
    ffmpeg/
  models/
  runtime/
  output/
  logs/
```

Runtime outputs stay inside the application folder: `output/`, `uploads/`, `work/`, `models/`, `.cache/`, `logs/`, and `tools/`.

## Desktop Shell Evaluation

### A. Browser Launcher Around Local Web Server

This is the recommended M13 direction. It has the lowest risk because it reuses the existing Web UI, keeps API and pipeline behavior unchanged, and gives users a predictable double-click or PowerShell startup path.

### B. Electron Wrapper

Electron would provide a familiar desktop window and richer packaging hooks, but it adds a larger runtime, Node process management, packaging complexity, and another surface for local server lifecycle issues.

### C. Tauri Wrapper

Tauri can produce a smaller desktop shell, but it introduces Rust/Tauri integration work and packaging decisions before the Web product surface is stable enough to justify that complexity.

## Recommendation

Continue with Browser launcher / local Web service for now. Defer Electron/Tauri implementation until portable release requirements are clearer.

No installer in M13. No release zip in M13. No code signing in M13. No auto-update in M13.
