# v0.3.3 Brand Rename

## Starting Commit

- `7e7c738 v0.3.2: add electron folder picker`

## New Brand

- Chinese name: `智译字幕工坊`
- English name: `CineSub Studio`
- Recommended display: `智译字幕工坊 / CineSub Studio`

## User-Visible Locations Updated

- `web/index.html`
  - Browser title: `智译字幕工坊 — AI 字幕生成器`
  - Sidebar primary title: `智译字幕工坊`
  - Sidebar subtitle: `CineSub Studio`
  - Tagline: `本地优先的视频字幕识别与大模型翻译工作台`
- `desktop/main.js`
  - Electron window title: `智译字幕工坊 / CineSub Studio`
  - Desktop startup/error display text where the product name is shown.
- `desktop/package.json`
  - Added display `productName`.
  - Updated user-facing description.
- `desktop/README.md`
  - Updated desktop shell title and product references.
- `README.md`
  - Updated main title and introductory product references.
- `docs/desktopization_readiness.md`
  - Updated user-facing product reference.
- `docs/windows_portable_quickstart.md`
  - Updated quickstart title.
- `docs/windows_portable_release_readiness.md`
  - Updated desktop/portable user-facing title.
- `tests/test_branding_text.py`
  - Added v0.3.3 brand rename regression coverage.

## Not Changed

- GitHub repository name.
- Python package paths.
- `src/`, `web/`, `config/`, `runtime/`, `output/`, `models/`, or other runtime/source directory structure.
- API endpoints or routes.
- Provider/Profile config schema.
- Backend, pipeline, ASR, translation, packaging, downloader, or Electron lifecycle behavior.
- Historical acceptance/audit documents.
- Internal technical IDs such as npm package name `cinesub-studio-desktop`.

## Test Results

- Passed: `.\.venv\Scripts\python.exe -B -m pytest tests\test_branding_text.py -q`
- Passed: `.\.venv\Scripts\python.exe -B -m pytest tests\test_premium_ui_refresh.py tests\test_electron_shell_readiness.py tests\test_electron_folder_picker.py -q`
- Passed: `.\.venv\Scripts\python.exe -B -m pytest tests -q`
  - Note: pytest emitted one existing background-thread warning in `tests/test_web_runtime_diagnostics_ui.py::test_ffmpeg_missing_and_version_probe_failure_do_not_raise`; the suite still passed.
- Passed: `.\start_web.ps1 -Smoke -NoBrowser -NonInteractive`
- Passed: `git diff --check`
  - Note: Git reported LF-to-CRLF working-copy warnings only; no whitespace errors were reported.

## Scope Confirmation

This is a user-visible brand text rename only. There are no backend, pipeline, ASR, translation, packaging, downloader, Provider/Profile ownership, config schema, API route, runtime path, or folder picker behavior changes.
