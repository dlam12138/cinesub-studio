# v0.5 Windows Zero-Config Installer Preview — Acceptance

## Starting Point

- Tag: `v0.4-desktop-preview`
- Commit: `b51dc96 v0.4: add tester onboarding note`

## Installer Strategy

Use `electron-builder` with Windows NSIS target to produce:

1. **Unpacked build** (`npm run pack:win`) for quick validation.
2. **NSIS installer** (`npm run dist:win`) for end-user installation.

The Electron shell detects `app.isPackaged` to switch from dev-mode Python resolution (`.venv`/system) to packaged-mode resolution (bundled runtime under `resources/app/`).

## Versions

- **electron-builder**: `26.1.0`
- **Electron**: `37.10.3`

## Python Runtime Packaging Strategy

**Staged portable Python 3.12 runtime**.

- `packaging/windows/prepare_runtime.py` copies the complete `tools/python/` runtime.
- Only dependency packages are overlaid from `.venv/Lib/site-packages`; the venv launcher and `pyvenv.cfg` are not shipped.
- The staged interpreter must import `tkinter`, `faster_whisper`, and `ctranslate2` in isolated mode before packaging.
- `desktop/package.json` copies the validated `packaging/windows/runtime/` staging tree.
- The Electron shell resolves the bundled Python executable from `resources/app/python/python.exe`.
- Dev mode continues to use `.venv/Scripts/python.exe` or system Python.

Future versions may switch to a trimmed portable Python + site-packages or PyInstaller.

## Bundled Component Paths (Packaged Layout)

```text
resources/app/
  backend/           ← start_app.py, src/, web/, config/
  python/            ← complete portable Python (python.exe, DLLs/, Lib/, tcl/)
  tools/
    ffmpeg/bin/      ← ffmpeg.exe, ffprobe.exe
    cuda/            ← GPU build only: cublas64_12.dll, cudnn*_9.dll, etc.
  THIRD_PARTY_NOTICES.md
```

## CUDA / cuBLAS / cuDNN

- **cuBLAS**: `cublas64_12.dll` expected in `tools/cuda/`.
- **cuDNN**: `cudnn*_9.dll` expected in `tools/cuda/`.
- Exact CUDA toolkit version depends on the `ctranslate2` wheel used in `.venv`.
- **Explicit note**: NVIDIA display driver is **not** bundled.

## Model Strategy

- **No Whisper model files are bundled** in the installer.
- Model directory in packaged mode: `%LOCALAPPDATA%\CineSubStudio\models`.
- Hugging Face cache: `%LOCALAPPDATA%\CineSubStudio\.cache\huggingface`.
- Runtime diagnostics show model missing guidance; app does not crash.

## User Data Paths (Packaged Mode)

```text
%LOCALAPPDATA%\CineSubStudio\
  models\          ← user-placed Whisper models
  output\          ← generated subtitles
  work\            ← pipeline work files
  logs\            ← backend logs
  .cache\          ← Hugging Face / pip caches
  uploads\         ← Web UI uploads

%APPDATA%\CineSubStudio\
  config\          ← providers.local.json, language_profiles.local.json
```

## Installer Artifacts

- **Installer exe name** (example): `智译字幕工坊 Setup 0.5.0.exe`
- **Validated size**: `264834595` bytes (`252.6 MB`)
- **SHA-256**: `EC827C1FB9F4EE8C7F74EF845FFA8A83CBED8299847A926A0DF0C37F0D34236B`
- **Installed app exe**: `CineSubStudio.exe`
- **Start Menu shortcut**: `智译字幕工坊`
- **Desktop shortcut**: optional (NSIS `createDesktopShortcut: true`)

## Unpacked App Validation Checklist

- [x] `npm run pack:win` completes without error.
- [x] `desktop/release-validation/win-unpacked/CineSubStudio.exe` exists.
- [ ] Launch unpacked exe: Electron window opens.
- [x] Backend starts automatically (no manual Python/npm/PowerShell runtime dependency).
- [x] The selected local port returns HTTP 200 (`7874` used for validation).
- [x] Diagnostics API loads and shows `packaged-python` source.
- [ ] Provider/Profile pages load.
- [ ] Recent jobs loads.
- [ ] Folder picker works.
- [x] FFmpeg detected from the packaged environment override.
- [x] Default CPU/auto build does not bundle CUDA.
- [ ] A separate `-RequireCuda` GPU build packages CUDA and passes smoke.
- [ ] Model missing shows guidance, not crash.
- [x] Closing app stops the backend process tree.

## Installer Validation Checklist

- [x] `npm run dist:win` completes without error.
- [ ] Installer exe launches and installs successfully.
- [ ] Launch from Start Menu or installed app exe.
- [ ] No manual Python required.
- [ ] No manual Node/npm required.
- [ ] No manual FFmpeg PATH setup required.
- [ ] For a `-RequireCuda` GPU build, CUDA runtime DLLs are packaged and on runtime PATH.
- [x] No Whisper model bundled.
- [ ] Model missing guidance is clear.
- [ ] User can place model separately.
- [ ] Provider/API Key can be configured in UI.
- [ ] Existing Web UI loads.
- [ ] Folder picker works.
- [ ] Closing app stops backend.

## Real Sample Result

- Performed: [ ] Yes  [x] No
- If yes: document model used, input file, source SRT non-empty, translated/bilingual SRT non-empty.
- Reason: v0.5.1 Debug closeout deliberately avoids model downloads, long media, and paid LLM API calls.

## Tests Run

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_windows_zero_config_installer_preview.py -q
```

Result: `25 passed`.

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_electron_shell_readiness.py tests\test_electron_folder_picker.py tests\test_translation_structured_output.py -q
```

Result: `23 passed`.

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests -q
```

Result: `384 passed` with `PytestUnhandledThreadExceptionWarning` promoted to an error.

## Generated Artifacts

- `desktop/release-validation/` — retained local unpacked and NSIS validation artifacts; ignored by `.gitignore`.
- `desktop/node_modules/` — ignored by `.gitignore`.
- `packaging/windows/runtime/` — reproducible staging generated before packaging and removed after final validation.

**None of the above are committed.**

## Known Limitations

- **No code signing** — Windows SmartScreen may warn.
- **No auto-update** — users must manually download newer installers.
- **No model bundle** — Whisper models must be placed/downloaded separately.
- **No NVIDIA driver bundle** — GPU acceleration requires pre-installed driver.
- **Preview installer** — not a final public release.
- **Runtime size** — the preview stages a complete portable Python runtime and dependency tree; future releases may trim unused modules after measured validation.

## Confirmation

- [x] No ASR algorithm changes.
- [x] No translation behavior changes except packaging-related path fixes.
- [x] No Provider/Profile ownership changes.
- [x] No UI redesign.
- [x] No TTS/dubbing added.
- [x] No model downloader added.
- [x] No FFmpeg downloader added.
- [x] No auto-update added.
