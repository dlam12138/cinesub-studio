# v0.2 Premium UI Refresh Acceptance

## Starting point

- Branch: `v0.2-premium-ui-refresh`
- Starting commit: `e1f57c3 v0.1.1: fix quality check detected-language warning`

## Design goal

Refresh CineSub Studio into a premium local desktop-style subtitle tool while keeping the existing Python Web backend and single-file frontend delivery.

Design direction: cinema control-room desktop shell with dark graphite surfaces, compact readiness cards, a left navigation rail, and focused workflow panels.

WhisperSubTranslate was used only as product-experience inspiration for local-first workflow, queue visibility, and desktop-app clarity. No code, CSS, screenshots, icons, layout, or assets were copied.

## What changed

- Replaced the old top tab strip with a desktop app shell:
  - `CineSub Studio / 字幕工坊`
  - `Local Web App`
  - `v0.2 Preview`
  - Sidebar navigation for `批量处理`, `单个处理`, `最近任务`, `运行环境`, `翻译接口`, `语言风格`
- Removed the old sidebar step numbers (`01` through `05`) so the rail reads as feature navigation rather than a numbered workflow.
- Split batch processing and single-file subtitles into independent sidebar entries; `批量处理` activates only the pipeline workspace, and `单个处理` activates only the single-file workspace.
- Moved recent jobs into a dedicated `最近任务` workspace without duplicating the original `jobQueuePanel` / `jobQueueList` DOM IDs.
- Added a read-only `缺失组件处理` guidance card to the runtime page for FFmpeg/model placement and environment-variable options.
- Polished runtime diagnostics, Provider settings, Language Profile settings, cards, badges, tables, progress surfaces, empty states, and inline/toast feedback.
- Fixed user-visible Chinese mojibake in `web/index.html` only.

## Preserved behavior

- No ASR behavior changes.
- No translation behavior changes.
- No pipeline semantics changes.
- No Provider/Profile ownership changes.
- ASR precedence remains unchanged.
- Batch processing keeps its existing buttons, DOM IDs, JS handlers, and `/api/pipeline/*` calls.
- Single-file processing keeps `jobForm`, `startBtn`, existing submit handler, form controls, and `/api/jobs` calls.
- Provider remains LLM/API-only and continues masking API keys.
- Language Profile continues owning ASR defaults, translation style, glossary, and subtitle preferences.

## Non-goals

- No Electron/Tauri.
- No frontend framework, npm build, CDN, React, Vue, Svelte, or Tailwind.
- No model downloader.
- No runtime downloader, model hub, automatic repair, or install button was added; missing components are documented as guidance only.
- No database.
- No installer/release work.
- No TTS, dubbing, voice clone, model hub, or model management UI.
- No copied external project code/assets.

## Validation

- `.\.venv\Scripts\python.exe -B -m pytest tests\test_premium_ui_refresh.py -q` passed.
- `.\.venv\Scripts\python.exe -B -m pytest tests\test_web_ui_productization.py tests\test_web_queue_history_progress_ui.py tests\test_web_runtime_diagnostics_ui.py tests\test_web_settings_center.py -q` passed.
- `.\.venv\Scripts\python.exe -B -m pytest tests\test_quality_checker_boilerplate.py -q` passed.
- Import smoke passed with documented `PYTHONPATH`.
- `.\.venv\Scripts\python.exe -B -m pytest tests -q` passed.
- `.\start_web.ps1 -Smoke -NoBrowser -NonInteractive` passed.
- Local HTTP smoke passed:
  - homepage `200`
  - `/api/runtime/diagnostics` `200`
- Visual smoke:
  - Started `.\start_web.ps1 -NoBrowser -NonInteractive`.
  - Captured page with Playwright using installed Microsoft Edge channel.
  - Confirmed visible navigation, batch start, single-file start, and no TTS/dubbing entry.

## Known limitations

- Playwright's bundled Chromium was not installed locally; screenshot smoke used the installed Microsoft Edge channel instead.
- Existing test suite still emits a known background-thread warning from `job_api.run_job` during full tests; assertions pass and this UI milestone did not change job execution code.
- Screenshots and runtime artifacts are generated only for manual review and are not intended for commit.
