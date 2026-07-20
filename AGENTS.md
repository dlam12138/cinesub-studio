# AGENTS.md

本文件面向 agent 和源码维护者。普通用户说明只写在 `README.md`。

## 当前交付边界

仓库只维护 GitHub 已上传的源码；正式二进制交付物只有：

```text
CineSubStudio-0.6.2-windows-x64-portable.zip
CineSubStudio-0.6.2-windows-x64-portable.zip.sha256
```

不要在源码或文档中恢复已退役的 NSIS、BAT/PowerShell 便携启动包、FunASR、WhisperX、ASR candidate、`mixed-route-v1` 或 segment routing 产品链路。

0.6.2 的 Electron 目录布局和构建接口是冻结基线。改变 EXE 启动契约、资源目录、数据目录或发布文件名时必须升级版本并同步测试。

## 代码结构

- `src/core/transcribe.py`：FFmpeg 抽音频、ASR 会话、三模式转写、SRT 与语言报告。
- `src/core/subtitle_translate.py`：SRT 解析、翻译策略、缓存和译文输出。
- `src/core/quality_checker.py`：格式、重复、低可信与人工复核报告。
- `src/pipeline/`：批量扫描、阶段执行、断点续跑、状态和输出规划。
- `src/config/`：Provider、Language Profile 与恢复逻辑。
- `src/web/`：本地 HTTP API、单任务、Pipeline、诊断、设置和字幕预览。
- `src/tools/runtime_paths.py`：源码与 packaged 路径解析的唯一入口。
- `src/tools/runtime_env.py`：Python、FFmpeg、CUDA、模型和离线资源诊断。
- `src/tools/ffmpeg_locator.py`：FFmpeg 查找的唯一入口。
- `web/index.html`：无 CDN、无 npm 构建的单文件前端。
- `desktop/`：Electron 壳，只负责启动后端、显示窗口和退出时清理进程。
- `scripts/build_portable_release.py`：唯一正式发布构建入口。

## 源码与 packaged 布局

源码模式：

- 应用根为仓库根目录。
- `.venv/`、`models/`、`tools/`、`input/`、`output/`、`work/`、`logs/` 和 `.cache/` 都位于仓库内。
- `start_web.ps1` 只调用 `.venv\Scripts\python.exe -B start_app.py`。

Electron 便携模式：

- 入口为 `CineSubStudio.exe`。
- 后端源码位于 `resources/app/backend/`。
- portable Python 位于 `resources/app/python/`。
- FFmpeg 与 CUDA 位于 `resources/app/tools/`。
- 模型、配置、API Key、缓存、日志和字幕产物全部位于 EXE 同级 `data/`。
- Electron userData、session cache 和日志必须定向到 `data/`，不得写 `%APPDATA%` 或 `%LOCALAPPDATA%`。
- 关闭 Electron 后必须终止 `start_app.py`、Web 后端及其子进程。

`runtime_paths.py` 必须同时支持两种布局且导入无副作用。不得在业务模块重复推导 packaged 路径。

## Git 与本地数据边界

不得提交：

```text
.venv/                 .cache/                 .tmp/
acceptance/            research/               .superdesign/
.claude/               .agents/
models/                tools/python/           tools/wheelhouse/
tools/ffmpeg/          tools/cuda/
input/                 output/                 work/
archive/               failed/                 logs/
reports/               uploads/                data/
config/providers.local.json
config/language_profiles.local.json
```

还不得提交 API Key、token、用户媒体、字幕产物、测试私有样本、构建 staging、EXE、DLL、模型或 Release ZIP。公开源码只保留 example 配置、代码、必要测试、许可证和当前用户/开发者文档。

## ASR 约束

产品只使用 faster-whisper：

- `auto`：整片自动检测，`language=None`。
- `fixed`：整片指定语言；缺少具体语言时拒绝启动。
- `multilingual`：一次抽音频，VAD 分块，每块独立检测，恢复时间偏移并合并。

兼容规则：

- 显式 CLI/Web 参数优先于 Language Profile。
- 未传 `asr_mode` 但传具体 `language` 时推断为 `fixed`。
- 都未传时使用 `auto`。
- `auto` 和 `multilingual` 不接受具体语言。

多语言模式复用单个模型实例和一次 FFmpeg 抽取；无有效语音必须明确失败。诊断信息不得触发候选竞争、自动改写、换模型、局部重跑或输出替换。

## 配置与安全

- Provider 只管理 API Key、API Base 和 LLM 模型；GET 只能返回 `api_key_masked`。
- Provider 写入必须使用临时文件加 `os.replace()`；空 API Key 表示保留旧值。
- Language Profile 管语言、ASR 参数、质检阈值、术语和翻译风格，不得保存 API Key。
- 前端提示词编辑入口冻结；后端 `translation_prompt`、Profile 字段和兼容测试保留。
- Web 只绑定 `127.0.0.1`，下载和预览不得接受任意文件路径。
- 日志、stderr、报告和诊断不得泄露完整密钥。

优先级：

1. CLI/Web 显式参数
2. Language Profile
3. Provider
4. 默认值

## Pipeline 与输出

- 后端 Pipeline 必须使用 `sys.executable -B src\pipeline\batch_worker.py --<action>`。
- 只读接口不得改变任务状态；同一时间只允许一个后台 Pipeline。
- `--retry-failed` 只处理 `status == "failed"`，不扫描新文件或重置其他任务。
- 完成任务只有在配置签名一致且最终产物存在、非空时才能跳过。
- stale/running-after-crash 只显示 warning，不自动 reset。
- 当前只稳定生成 SRT。ASS 参数仍可传递，但必须明确说明未生成 `.ass`。

## FFmpeg、CUDA 与缓存

- `ffmpeg_locator.py` 是唯一 FFmpeg 入口，不得直接调用裸 `ffmpeg`。
- 不修改系统 PATH；CUDA DLL 只通过当前进程或子进程环境注入。
- `device=auto` 优先 CUDA，环境不兼容时回退 CPU；显式 `cuda` 必须快速失败并给出诊断。
- 模型和缓存必须留在当前布局的项目目录或 `data/`，不得写入 C 盘全局缓存。
- 大组件下载前必须显示大小、来源和目标，不得静默下载 CUDA、portable Python、wheelhouse 或大模型。

## Electron 发布

正式构建：

```powershell
.\.venv\Scripts\python.exe -B scripts\build_portable_release.py
```

构建器必须：

- 验证 portable Python、faster-whisper、CTranslate2、PyAV、NumPy、FFmpeg、FFprobe、CUDA DLL 和 `small`。
- 调用 `electron-builder --win --dir`，不生成 NSIS 或自动更新产物。
- 生成单顶层目录、`release_manifest.json`、包内文件 SHA256 和包外 ZIP SHA256。
- 排除缓存、本地配置、用户数据、测试媒体、内部资料、启动脚本、`ffplay.exe` 和 `large-v3`。
- 在单文件达到 GitHub 2 GiB 限制时生成 CPU 主包和 CUDA add-on，不得静默删减模型或 DLL。

发布前必须在中文和空格路径解压，直接启动 EXE，验证 packaged 诊断、`local_files_only=True`、三种 ASR 模式和退出进程清理。

## 测试命令

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -m pytest -q
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file, runtime_env, runtime_paths, subtitle_model, runtime_api, pipeline_api; print('imports ok')"
.\.venv\Scripts\python.exe -B src\core\subtitle_translate.py --self-test
.\.venv\Scripts\python.exe -B src\core\quality_checker.py --self-test
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
node --check desktop/main.js
node --check desktop/preload.js
node --check desktop/launch.js
git diff --check
```

涉及 Web 时还要确认 `/` 和 `/api/runtime/diagnostics` 返回 200。涉及模型时必须明确使用 `local_files_only=True`，不得为测试触发下载。

## Review 标准

优先检查：

- 是否可能覆盖、删除或移动用户输入和字幕。
- 是否写入全局 PATH、代理、系统 Python、APPDATA 或 C 盘缓存。
- 中文路径、空格路径、绝对路径和上传文件是否可用。
- 前端选项与 CLI/API/Profile 是否一致。
- 失败是否包含用户可执行的处理建议和可诊断细节。
- GitHub 源码、根 README、包内 README、Release 文件名和实际 ZIP 布局是否一致。
