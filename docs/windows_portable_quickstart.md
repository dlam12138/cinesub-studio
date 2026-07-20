# 智译字幕工坊 / CineSub Studio Windows Portable Quickstart

This quickstart describes the M14 staged portable folder. It is not an official release package and it is not fully standalone.

## Start

1. Put the staged folder in a writable location.
2. Make sure the required Python environment exists. M14 does not bundle Python.
3. Put FFmpeg at `tools/ffmpeg/bin/` or set `CINESUB_FFMPEG` / `FFMPEG_PATH`.
4. Put ASR models under `models/` or the supported project-local Hugging Face cache.
5. Run:

```powershell
.\start_web.ps1
```

If the browser does not open, visit:

```text
http://127.0.0.1:7860/
```

## Validate

Run:

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
```

Smoke mode should not open a browser, ask for input, download models, call translation APIs, load ASR models, or process media.

## Configure

Use the Web runtime diagnostics tab to check readiness. Then configure:

- translation Provider
- Language Profile
- local FFmpeg/model placement

Provider API keys are runtime config only. They must not be copied from the source tree into a staged package.

## Limitations

M14 portable readiness does not bundle Python. A future milestone may evaluate embedded Python, release-candidate packaging, or installer packaging.

M14 does not create an official release artifact.
