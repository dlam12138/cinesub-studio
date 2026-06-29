# AGENTS.md

## 项目结构

### 源码目录 (`src/`)

- `src/core/`
  - `transcribe.py`: 核心识别脚本。负责 ffmpeg 抽音频、加载 faster-whisper、输出 SRT、保存语言识别 JSON。
  - `subtitle_translate.py`: 字幕翻译模块。解析 SRT、调用 LLM API、输出双语/译文 SRT、翻译缓存。
  - `quality_checker.py`: 自动质检模块。SRT 格式检查 + 翻译质量检查 + 生成质量报告和 review_needed.srt。
- `src/pipeline/`
  - `batch_worker.py`: 批量生产流水线。自动发现视频 → 提取音频 → 转写 → 翻译 → 质检 → 归档，支持断点续跑和状态追踪。
- `src/config/`
  - `provider_store.py`: Provider 配置管理模块。负责 config/providers.local.json 的读写、脱敏、原子写入。
  - `language_profile_store.py`: Language Profile 配置管理模块。负责 config/language_profiles.local.json 的读写、内置 3 个默认 profile。
- `src/web/`
  - `web_server.py`: 本地 Web 后台。负责上传、本机路径任务、任务状态、日志、下载 SRT、Provider 管理 API。
- `src/tools/`
  - `download_model_file.py`: 直接下载模型文件的兜底工具。
  - `download_ffmpeg.py`: Python FFmpeg 下载兜底工具，安装到 `tools/ffmpeg/bin/`。
  - `analyze_subtitles_workflow.py`: 字幕风格分析工作流入口，负责扫描、调用 analyzer、生成 prompt。
- `src/__init__.py`: 路径注入，将 `src/` 下各子目录加入 `sys.path`，确保跨模块导入可用。

### 入口脚本（根目录）

- `start_app.py`: 双击启动器。启动 Web 服务、自动打开浏览器、显示状态窗口。
- `run_transcribe.ps1`: 命令行识别入口。
- `start_web.ps1`: Web 控制台 Windows 便利入口，薄包装调用 `start_app.py`，默认服务地址 `http://127.0.0.1:7860`。
- `install.ps1`: 安装和重建 `.venv`，pip 缓存固定到 `.cache\pip`。
- `analyze_subtitles.ps1`: Windows 便利入口，只调用 `src/tools/analyze_subtitles_workflow.py`。

### 前端

- `web/index.html`: 本地前端页面。四个标签页：流水线控制台、单文件处理、模型接口、语言配置。不要引入构建工具，保持单文件静态页面。

### 运行产物（不要当源码修改）

- `models/`, `.cache/`, `uploads/`, `output/`, `work/`, `input/`, `archive/`, `failed/`, `.venv/`, `tools/`, `logs/`, `config/`: 运行产物，不要当源码修改。

## 运行命令

```powershell
cd D:\Claude项目操作\电影翻译
.\start_web.ps1
```

```powershell
.\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model small -Device cpu
```

首次安装或重建环境：

```powershell
.\install.ps1
.\install.ps1 -Python py -PythonArgs "-3.12" -Recreate
```

## 测试命令

基础导入检查（全部模块），避免写 `__pycache__`：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B -c "import transcribe, subtitle_translate, quality_checker, batch_worker, web_server, download_model_file; print('imports ok')"
```

自测：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\core\subtitle_translate.py --self-test
.\.venv\Scripts\python.exe -B src\core\quality_checker.py --self-test
```

Web 服务检查：

```powershell
.\start_web.ps1
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/ | Select-Object -ExpandProperty StatusCode
```

批量流水线检查：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --scan
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --status
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --review
```

功能验收时优先用短音视频样本跑 `small + cpu + int8 + local_files_only`，确认 `output/*.srt` 生成且时间轴格式为 `HH:MM:SS,mmm --> HH:MM:SS,mmm`。

## Pipeline 控制台

Web 控制台现在包含两个标签页：

### 流水线控制台（默认）
- 扫描 input：调用 `GET /api/pipeline/scan` → 后端执行 `batch_worker.py --scan`
- 查看状态：调用 `GET /api/pipeline/status` → 后端执行 `batch_worker.py --status`
- 异常复核：调用 `GET /api/pipeline/review` → 后端执行 `batch_worker.py --review`
- 重试失败：调用 `POST /api/pipeline/retry-failed` → 后台线程执行 `batch_worker.py --retry-failed`，日志写入 `logs/pipeline.log`
- 刷新全部：依次调用以上三个 GET 接口
- 操作日志：实时显示前端操作记录

### 单文件处理
- 保留原有的上传、转写、翻译、下载 SRT 全部功能
- 通过 `POST /api/jobs` 创建任务，`GET /api/jobs/<id>` 轮询状态

### Pipeline API 测试

```powershell
# 启动服务
.\start_web.ps1

# 测试各 API 端点（另一个终端）
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/api/pipeline/scan | Select-Object StatusCode
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/api/pipeline/status | Select-Object StatusCode
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/api/pipeline/review | Select-Object StatusCode
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/api/pipeline/logs | Select-Object StatusCode
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/api/jobs | Select-Object StatusCode
```

### Pipeline 控制台约束

**命令执行：**
- 所有 pipeline 命令通过 `sys.executable -B src\pipeline\batch_worker.py --<action>` 执行
- 不要硬编码 `python` 或 `python3`
- scan/status/review 同步执行（30s 超时），run/retry-failed 后台执行

**语义隔离（关键）：**
- `POST /api/pipeline/run` → 执行 `--input` 完整流水线（扫描 + 处理全部 input）
- `POST /api/pipeline/retry-failed` → 仅执行 `--retry-failed`，只重试失败任务，**不扫描新文件**
- 两者互斥，不可混用。绝对不要在 `retry-failed` 里调用 `pipeline.run()` 或 `scan()`

**并发保护：**
- 同一时间只允许一个后台流水线任务运行
- 重复点击返回 HTTP 409，前端必须展示提示
- 避免多个 `batch_worker.py` 同时运行导致的 .state.json 冲突、文件移动冲突、LLM API 重复消耗

**只读保证：**
- `GET /api/pipeline/scan`、`status`、`review`、`logs`、`task` 只能读取，不得修改文件
- 日志不存在时 `/api/pipeline/logs` 返回 `{"ok": true, "lines": [], "text": ""}`，不报错

**安全：**
- Web 服务只绑定 `127.0.0.1`，不要默认暴露到局域网
- 不要破坏原有 Web UI（POST /api/jobs、GET /download 等）

**前端：**
- 不引入 React/Vue/CDN/npm，保持单文件 HTML
- 不要另起端口服务，复用 7860
- 命令失败时必须展示 stderr 和 returncode
- 按钮命名：扫描 input / 开始处理 input / 重试失败（明确区分语义）

**日志：**
- 后台任务日志写入 `logs/pipeline.log`
- 默认只返回最近 200 行
- TODO: 日志超过 5MB 时轮转为 `pipeline.log.1`

### Provider 管理约束

**安全：**
- `config/providers.local.json` 已 gitignore，绝对不要提交
- `GET /api/providers` 和 `GET /api/providers/active` 必须返回脱敏后的 api_key_masked
- 后端日志、错误消息、stdout 决不输出完整 API Key
- 编辑 Provider 时，api_key 为空则保留旧值，不要覆盖为空

**原子写入：**
- 写配置必须先写 `.tmp` 文件再 `os.replace()` 到目标
- 避免写一半导致配置损坏

**CLI 优先级（严格顺序）：**
1. CLI 显式参数（--api-key 等）
2. --provider <id> 指定的 Provider 配置
3. active provider
4. 程序默认值

**集成约束：**
- `POST /api/pipeline/run` 和 `POST /api/pipeline/retry-failed` 默认使用 active provider
- Provider 配置包含 Whisper 模型和设备设置（whisper_model, whisper_device）
- `provider_store.py` 是独立的纯 Python 模块，不依赖 web_server
- 前端 Provider 下拉框不覆盖用户手动输入

**测试连接：**
- 仅支持 OpenAI-compatible（POST {base}/chat/completions）
- 发送极短消息，max_tokens=5，15s 超时
- 错误消息中不包含 API Key

### Language Profile 约束

**职责边界：**
- Provider 管 API Key / API Base / LLM 模型
- Language Profile 管语言 / ASR 参数 / 质检阈值 / 翻译风格
- **绝对不要把 API Key 写进 Language Profile**

**内置默认：**
- 内置 3 个 profile: auto-detect / fr-film / generic-european-film
- 配置文件缺失时必须返回这 3 个默认值，不能报错
- 本地配置可覆盖内置 profile（相同 id）
- 禁止硬删除内置 profile，只能删除本地覆盖版本

**优先级：**
1. CLI 显式参数 > Language Profile > Provider > 默认值

**集成要求：**
- POST /api/pipeline/run 和 retry-failed 必须传递 language_profile_id
- batch_worker.py --language-profile <id> 必须可用
- transcribe.py 读取 profile ASR 参数，.lang.json 包含 language_profile 字段
- quality_checker.py 读取 profile 质检阈值
- 翻译阶段读取 profile translation_style 附加到 prompt

**Web UI：**
- 第 4 个标签页管理 profiles，Pipeline/单文件处理均有下拉框
- 不得破坏原有 3 个标签页

**配置安全：**
- config/language_profiles.local.json 已 gitignore，原子写入

## 代码风格

- Python 使用标准库优先；新增第三方依赖必须写入 `requirements.txt`，并说明为什么不能用标准库。
- 路径使用 `pathlib.Path`；传给外部命令时再转 `str`。
- Windows 路径要支持中文目录；不要手写字符串拼接路径。
- Web 后台保持无框架标准库实现，除非明确要迁移框架。
- 前端保持单页静态 HTML/CSS/JS；不引入 npm、打包器或 CDN。
- 日志必须能让用户判断当前阶段：抽音频、加载模型、识别、输出、失败原因。
- 默认不要生成 Python 字节码检查；用 `python -B`。

## 禁止事项

- 不要删除或清空用户的视频、音频、字幕、`output/` 成品。
- 不要全局修改 Windows 代理、系统环境变量、Python 安装、PATH 或 Codex 配置。
- 不要把模型、缓存、上传文件移到 C 盘；模型和缓存必须留在当前项目目录。
- 不要提交或手动编辑 `.venv/`, `.cache/`, `models/`, `uploads/`, `work/`, `output/`, `__pycache__/`。
- 不要默认下载大模型；新增下载行为必须可选，并说明大小和目标目录。
- 不要在识别子进程外清理 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`；代理清理只允许作用于本项目启动的识别子进程。
- 不要把失败吞掉；所有后台失败必须回到 Web 日志。

## FFmpeg 管理约束

- 项目应优先使用内置 FFmpeg。
- 不要求用户手动安装 FFmpeg 或配置系统 PATH。
- Windows 下载脚本为 `scripts/download_ffmpeg.ps1`。
- 下载后的二进制放在 `tools/ffmpeg/bin/`。
- 不得提交 `ffmpeg.exe`、`ffprobe.exe`、`ffplay.exe`。
- 不得提交 `.tmp/` 下载缓存。
- `ffmpeg_locator.py` 是唯一 FFmpeg 查找入口。
- 新增代码不得绕过 `ffmpeg_locator.py` 直接调用裸 `ffmpeg`。

## PowerShell 管理约束

- PowerShell 只作为 Windows 启动器、安装器和便利入口。
- 修改功能时优先改 Python 模块、Python CLI 或 Web 后端，不要把新业务逻辑放进 `.ps1`。
- 复杂流程、状态判断、路径扫描、JSON 读写、日志解析、进程调度必须优先放在 Python。
- `.ps1` 不得直接管理后台流水线任务，不得用 `Start-Process` 维持核心服务。
- `.ps1` 不得要求系统 PATH 中已有 FFmpeg；运行时由 `ffmpeg_locator.py` 查找。
- `.ps1` 不得打印 API Key、token、secret 或完整 Provider 配置。
- `start_web.ps1` 应保持薄包装，调用 `.venv\Scripts\python.exe -B start_app.py`。
- `analyze_subtitles.ps1` 应保持薄包装，业务流程由 `src/tools/analyze_subtitles_workflow.py` 承担。
- 如确需新增 PowerShell 文件，必须说明为什么不能用 Python，并保持为可替换的薄入口。

## 完成标准

- CLI 和 Web 两条入口都不能被改坏。
- 所有新增选项必须同时考虑 CLI、Web 后台和前端展示；只做一半不算完成。
- 运行产物必须仍写入 `output/`, `uploads/`, `work/`, `models/`, `.cache/` 对应目录。
- 修改后至少通过基础导入检查。
- 如果涉及 Web，必须确认 `http://127.0.0.1:7860/` 返回 `200`。
- 如果涉及模型加载或下载，必须说明是否已验证 `local_files_only=True`。

## Review 标准

- 优先检查用户数据风险：是否可能覆盖、删除、移动输入文件或输出字幕。
- 检查 C 盘占用风险：是否新增默认缓存、模型或临时文件到用户目录。
- 检查代理影响范围：是否误改全局代理或 Codex 所需环境。
- 检查长任务体验：Web 日志是否能看到进度，失败是否可诊断。
- 检查路径兼容：中文路径、空格路径、本机绝对路径和上传文件都应可用。
- 检查模型行为：首次下载、离线本地加载、镜像源、`local_files_only` 都不能互相冲突。
- 检查前端选项与后台参数是否一一对应。
