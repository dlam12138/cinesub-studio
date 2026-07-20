# M9 Web UI Productization MVP

## Summary

M9 starts after M8 closeout (`12232e4`).

The goal is to turn the existing Web UI from an engineering/debug console into a clearer user-facing workflow for subtitle generation. This milestone is UI/productization-focused only. The pipeline core remains unchanged.

## What Changed

### Web UI (`web/index.html`)

1. **Product title**: "CineSub Studio Pipeline" → "字幕工坊 — 视频字幕生成器"
2. **Workflow subtitle**: "流水线识别、翻译、质检" → "选择视频 → 语音识别 → 翻译 → 获取字幕"
3. **Tab labels** updated for clarity:
   - "流水线控制台" → "📁 批量处理"
   - "单文件处理" → "🎬 单文件字幕"
   - "模型接口" → "🔑 翻译接口"
   - "语言配置" → "🌐 语言风格"
4. **Step-by-step section headers** in single-file tab:
   - Step 1 · 选择视频
   - Step 2 · 语音识别
   - Step 3 · 翻译设置
   - Step 4 · 任务状态与结果
5. **Field labels** simplified:
   - "模型" → "语音识别模型"
   - "设备" → "运行设备"
   - "源语言" → "视频语言"
   - "模型接口" → "翻译接口"
   - "语言配置" → "语言风格"
   - "输入目录" → "视频目录"
6. **Workflow hint** added to single-file tab: "选择视频 → 配置识别参数 → 选择翻译设置 → 点击开始识别"
7. **Job result display** improved:
   - Added `renderJobResult()` JavaScript function
   - Shows completed task outputs (source/translated SRT paths)
   - Shows failed task error summary with last log lines
   - Added hidden `#job-result` container in the markup
8. **Pipeline button labels** clarified:
   - "扫描" → "🔍 扫描文件"
   - "开始处理" → "▶ 开始批量处理"

### Backend Changes

None. M9 is purely UI/productization. No Python source files were modified.

### Tests Added

`tests/test_web_ui_productization.py` — 18 tests covering:

- Page title and product identity
- Workflow sections (input, ASR, translation, output, status)
- Step labels (Step 1–4)
- Provider modal/table do not expose `whisper_model` / `whisper_device`
- Job API returns expected fields (`id`, `status`, `options`, `source_output`, `translated_output`, `logs`)
- `get_job` strips internal fields (`_api_key`)
- Failed job includes readable `logs` and `status`
- Provider / Language Profile selectors exist in DOM
- UI does not expose dubbing / TTS / voice cloning / lip-sync / audio mixing controls
- UI does not expose model management features

## Validation Results

### Test Runs

```powershell
. .venv\Scripts\python.exe -B -m pytest tests\test_web_ui_productization.py -q
```

**Result:** 18 passed

```powershell
. .venv\Scripts\python.exe -B -m pytest tests\test_provider_language_profile_asr_boundary.py tests\test_effective_translation_config.py tests\test_language_profile_glossary.py -q
```

**Result:** 11 passed

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web\src\tools"
. .venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

**Result:** `imports ok`

```powershell
. .venv\Scripts\python.exe -B -m pytest tests -q
```

**Result:** 245+ passed (full suite passes)

### Git Checks

```powershell
git diff --check
```

**Result:** No whitespace errors.

## Provider / Language Profile / ASR Boundaries

M9 respects the M8.9 rule:

```text
CLI explicit ASR args > Language Profile ASR settings > built-in defaults
```

- Provider remains LLM-only. No ASR fields were added to Provider UI or config.
- The Provider modal and table do not expose `whisper_model` or `whisper_device`.
- Language Profile still owns ASR defaults (model, device, compute_type, etc.).
- The single-file tab auto-fills ASR fields from Language Profile selection, preserving existing behavior.

## Job Status and Error Display

- Job status shows: `等待开始` / `排队中` / `处理中` / `已完成` / `处理失败`
- Failed jobs display the last 3 log lines as an error summary
- Completed jobs show output file paths for source SRT, translated SRT, and quality report
- `get_job()` strips internal `_api_key` before returning

## Output Visibility

- Completed single-file jobs show download links for source and translated SRT
- Pipeline task cards show artifact chips with download links, file sizes, and copy-path buttons
- Pipeline progress shows overall completion percentage and per-task status

## Explicit Non-Goals

- No pipeline rewrite
- No database introduced
- No desktop packaging (Electron, Tauri)
- No Chinese dubbing / TTS / voice cloning
- No audio mixing, video muxing, or lip-sync
- No model auto-download beyond existing support
- No release artifact change
- No M10 work started
