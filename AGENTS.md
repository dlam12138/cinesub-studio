# AGENTS.md

本文件只面向 agent 和后续维护者，不面向普通用户。用户说明写在 `README.md`。

## 项目结构

源码目录：

- `src/core/transcribe.py`：转写入口，负责 FFmpeg 抽音频、faster-whisper 加载、SRT 输出、语言检测 JSON。
- `src/core/subtitle_model.py`：统一字幕模型、格式注册和输出规划接口；当前 `srt` 启用，`ass` 只预留。
- `src/core/subtitle_translate.py`：SRT 解析、LLM 翻译、双语/译文 SRT、翻译缓存。
- `src/core/quality_checker.py`：SRT 格式检查、翻译质量检查、质量报告、`review_needed.srt`。
- `src/pipeline/batch_worker.py`：批量流水线，扫描输入、抽音频、转写、翻译、质检、归档、断点续跑。
- `src/config/provider_store.py`：Provider 配置读写、脱敏、原子写入。
- `src/config/language_profile_store.py`：Language Profile 配置读写和内置 profile。
- `src/web/web_server.py`：本地 Web 后端，处理任务、下载、Provider、Language Profile、Pipeline、运行环境 API。
- `src/web/runtime_api.py`：运行环境 Web API 的薄模块，转调 `runtime_env.py`。
- `src/web/pipeline_api.py`：Pipeline Web API 辅助模块，负责只读状态、日志、后台任务启动和子进程环境。
- `src/tools/ffmpeg_locator.py`：唯一 FFmpeg 查找入口。
- `src/tools/runtime_env.py`：统一运行环境管理，检测 Python、FFmpeg、CUDA、wheelhouse、模型，处理离线包导入和环境下载计划。
- `src/tools/download_ffmpeg.py`：FFmpeg 下载兜底工具，写入 `tools/ffmpeg/bin/`。

入口脚本：

- `start_app.py`：双击启动器，启动 Web 服务并打开浏览器。
- `start_web.ps1`：薄包装，调用 `.venv\Scripts\python.exe -B start_app.py`。
- `run_transcribe.ps1`：单文件转写便利入口。
- `install.ps1`：创建/重建 `.venv`，支持项目内 `tools/python/python.exe` 和 `tools/wheelhouse/` 离线安装。
- `analyze_subtitles.ps1`：薄包装，调用 `src/tools/analyze_subtitles_workflow.py`。

前端：

- `web/index.html`：单文件静态页面，不引入 React/Vue/CDN/npm。

## 运行产物边界

不要把以下目录当作源码修改或提交：

```text
.venv/
.cache/
.tmp/
models/
uploads/
output/
work/
input/
archive/
failed/
logs/
reports/
tools/python/
tools/wheelhouse/
tools/ffmpeg/
tools/cuda/
```

`src/tools/` 是源码目录，不能和根目录 `tools/` 运行产物混淆。

## 环境策略

- 设备默认语义是 `auto`：CUDA 可用时优先 CUDA，否则 CPU 兜底。
- 诊断必须区分项目内 `.venv` 和项目内 portable Python。当前 `.venv` 可能由系统 Python 创建；只有 `tools/python/python.exe` 存在并用于重建 `.venv` 后，才算 portable Python 路径。
- 推荐 portable Python 版本锁定 3.12。不要自动重建用户当前可用 `.venv`。
- 明确选择 `cuda` 时必须严格检查 CUDA 环境；缺 DLL、驱动或依赖时快速失败并给出可诊断错误。
- CUDA DLL 只允许通过当前进程或子进程环境注入，不修改系统 PATH。
- `runtime_env.py` 是运行环境检测、CUDA 注入、离线包导入、下载计划的统一入口；新增代码不要重复硬编码 `tools/cuda` 诊断。
- 模型和 Hugging Face 缓存必须留在项目内 `models/` 与 `.cache/huggingface/`。
- pip 缓存必须留在项目内 `.cache/pip/`。
- 离线包只允许解压到 `tools/python/`、`tools/wheelhouse/`、`tools/ffmpeg/`、`tools/cuda/`、`models/`。
- 一键下载大组件前必须展示大小、来源和目标目录；CUDA、portable Python、wheelhouse、大模型不得静默下载。

## FFmpeg 约束

- 项目优先使用内置 FFmpeg。
- 不要求用户手动安装 FFmpeg 或配置系统 PATH。
- `ffmpeg_locator.py` 是唯一查找入口。
- 新代码不得直接调用裸 `ffmpeg`。
- 下载后的二进制放在 `tools/ffmpeg/bin/`。
- 不得提交 `ffmpeg.exe`、`ffprobe.exe`、`ffplay.exe`。

## Provider 约束

- `config/providers.local.json` 已 gitignore，绝对不要提交。
- GET 接口必须返回脱敏后的 `api_key_masked`。
- 日志、错误、stdout 不得输出完整 API Key。
- 编辑 Provider 时，`api_key` 为空表示保留旧值。
- Provider 只管 API Key / API Base / LLM 模型。
- Provider 配置写入必须使用临时文件加 `os.replace()`。

CLI 优先级：

1. CLI 显式参数
2. `--provider <id>` 指定配置
3. active provider
4. 程序默认值

## Language Profile 约束

- Language Profile 管语言、ASR 参数、质检阈值和翻译风格。
- Language Profile 可预留 `subtitle_style`，包含 `formats` 和 `ass_style_id`。样式配置不属于 Provider。
- 绝对不要把 API Key 写入 Language Profile。
- 内置 profile 缺配置文件时必须可用。
- 本地配置可覆盖内置 profile。
- 禁止硬删除内置 profile，只能删除本地覆盖版本。

优先级：

1. CLI 显式参数
2. Language Profile
3. Provider
4. 默认值

## Pipeline 约束

- 后端执行 pipeline 必须使用 `sys.executable -B src\pipeline\batch_worker.py --<action>`。
- 不要硬编码 `python` 或 `python3`。
- `GET /api/pipeline/scan`、`status`、`review`、`logs`、`task` 必须只读。
- `POST /api/pipeline/run` 执行完整 input 流水线。
- `POST /api/pipeline/retry-failed` 只重试失败任务，不扫描新文件。
- 同一时间只允许一个后台流水线任务运行；冲突返回 HTTP 409。
- 后台日志写入 `logs/pipeline.log`。

## 字幕格式与 ASS 预留

- 默认只启用 `srt`，不得在当前版本实际写 `.ass` 文件。
- `subtitle_formats`、`ass_style_id`、`subtitle_style` 是预留参数，CLI/Web/Pipeline 可以接收并传递。
- 请求 `ass` 时必须返回或记录明确状态：`ASS output is reserved for a future version; no .ass file was generated.`
- `output/ass/` 只是未来输出规划路径；当前实现不要为了预留而创建实际 ASS 成品。
- 后续真正实现 ASS 时，应优先扩展 `src/core/subtitle_model.py` 的格式规划/渲染接口，避免把 ASS 逻辑塞进 `web_server.py` 或 `batch_worker.py`。

## 维护拆分方向

- `web_server.py` 已先拆出 `runtime_api.py` 和 `pipeline_api.py`；后续可继续拆 `job_api.py`、`provider_profile_api.py`、`storage_api.py`，主文件只保留 HTTP 分发和通用响应能力。
- `web/index.html` 必须保持单文件交付，但 JS 内部应按 runtime、pipeline、single-file、provider、language-profile、storage 分区维护。
- `batch_worker.py` 后续应继续拆出输出路径规划、任务状态持久化、阶段执行 helper。新增字幕格式时优先走 `subtitle_model.py`，减少对批量流水线主流程的改动。

## Web 约束

- Web 服务只绑定 `127.0.0.1`。
- 不引入前端构建工具、npm、CDN。
- 不破坏已有 `POST /api/jobs`、`GET /download`、Provider、Language Profile API。
- 命令失败时前端必须展示 stderr 或 returncode。
- 运行环境 UI 必须明确区分：诊断、离线包导入、下载计划、实际下载。

## PowerShell 约束

- PowerShell 只作为 Windows 启动器、安装器和便利入口。
- 复杂流程、状态判断、路径扫描、JSON 读写、日志解析、进程调度优先放 Python。
- `.ps1` 不得直接管理后台流水线任务。
- `.ps1` 不得要求系统 PATH 已有 FFmpeg。
- `.ps1` 不得打印 API Key、token、secret 或完整 Provider 配置。
- 新增 PowerShell 文件必须说明为什么不能用 Python，并保持薄入口。

## 测试命令

基础导入检查：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
```

自测：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\core\subtitle_translate.py --self-test
.\.venv\Scripts\python.exe -B src\core\quality_checker.py --self-test
```

运行环境诊断：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\tools\runtime_env.py diagnostics
```

Pipeline 只读检查：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --scan
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --status
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --review
```

Web 检查：

```powershell
.\start_web.ps1
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/ | Select-Object -ExpandProperty StatusCode
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/api/runtime/diagnostics | Select-Object -ExpandProperty StatusCode
```

## 完成标准

- CLI 和 Web 两条入口都不能被改坏。
- 涉及 Web 的改动必须确认首页 200 和运行环境诊断 API 可用。
- 涉及模型加载或下载时，必须说明是否验证 `local_files_only=True`。
- 运行产物仍写入 `output/`、`uploads/`、`work/`、`models/`、`.cache/`、`tools/` 对应目录。
- 基础导入检查必须通过。

## Diagnostics API 稳定字段

`GET /api/runtime/diagnostics` 的用户可读诊断结构是产品能力，不是临时调试输出。后续维护不要误删这些字段：

- `ffmpeg_source`
- `diagnostic_summary`
- `diagnostic_items`
- `diagnostic_items[].status`
- `diagnostic_items[].blocking`

`diagnostic_summary.status` 可以是 `ok`、`warning`、`error` 或 `not_configured`。当前 Python 版本不在推荐范围但基础流程可运行时，应保持 `warning`，不要误改为阻断错误。

## Pipeline 恢复约束

- `--retry-failed` 只重试 `status == "failed"` 的任务。
- `--retry-failed` 不扫描 input，不加入新文件，不重置 `completed`、`pending` 或 `running`。
- `completed` 只有在状态完成且当前配置对应的最终产物存在、非空时才可静默跳过。
- `skip_completed` 表示跳过整任务；stage reuse 表示未完成任务复用已有有效中间产物。
- stale/running-after-crash 只作为 warning 展示，不自动 reset。
- Web 和 CLI 必须共用同一套 retry 判断，不允许 Web 另写重试逻辑。

## Review 标准

优先检查：

- 是否可能覆盖、删除、移动用户输入视频或输出字幕。
- 是否新增 C 盘缓存、模型或临时文件。
- 是否误改全局代理、PATH、系统 Python 或 Codex 配置。
- 长任务是否有日志和可诊断失败。
- 中文路径、空格路径、本机绝对路径、上传文件是否可用。
- CUDA 首次使用、离线加载、镜像源、`local_files_only` 是否互相冲突。
- 前端选项和后端参数是否一一对应。
