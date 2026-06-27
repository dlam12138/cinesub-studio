# AGENTS.md

## 项目结构

- `transcribe.py`: 核心识别脚本。负责 ffmpeg 抽音频、加载 faster-whisper、输出 SRT。
- `web_server.py`: 本地 Web 后台。负责上传、本机路径任务、任务状态、日志、下载 SRT。
- `web/index.html`: 本地前端页面。不要引入构建工具，保持单文件静态页面。
- `run_transcribe.ps1`: 命令行识别入口。
- `start_web.ps1`: Web 控制台入口，默认服务地址 `http://127.0.0.1:7860`。
- `install.ps1`: 安装和重建 `.venv`，pip 缓存固定到 `.cache\pip`。
- `download_model_file.py`: 直接下载模型文件的兜底工具。
- `models/`, `.cache/`, `uploads/`, `output/`, `work/`, `.venv/`: 运行产物，不要当源码修改。

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

基础导入检查，避免写 `__pycache__`：

```powershell
.\.venv\Scripts\python.exe -B -c "import transcribe, web_server, download_model_file; print('imports ok')"
```

Web 服务检查：

```powershell
.\start_web.ps1
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:7860/ | Select-Object -ExpandProperty StatusCode
```

本地 `small` 模型加载检查：

```powershell
$env:HF_HOME='D:\Claude项目操作\电影翻译\.cache\huggingface'
$env:HF_HUB_CACHE='D:\Claude项目操作\电影翻译\.cache\huggingface\hub'
.\.venv\Scripts\python.exe -B -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root='D:/Claude项目操作/电影翻译/models', local_files_only=True); print('local small model loads')"
```

功能验收时优先用短音视频样本跑 `small + cpu + int8 + local_files_only`，确认 `output/*.srt` 生成且时间轴格式为 `HH:MM:SS,mmm --> HH:MM:SS,mmm`。

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
