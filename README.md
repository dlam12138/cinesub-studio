# CineSub Studio

CineSub Studio 是一个 Windows 本地影视字幕工具：把视频或音频转写为 SRT 字幕，可调用 OpenAI-compatible 翻译接口生成中文字幕或双语字幕，并输出基础质量报告和 `review_needed.srt`。

当前版本面向本机使用。它不要求修改系统 PATH，也不要求安装前端构建工具、npm、CDN 或浏览器插件。

## 1. 选择运行方式

CineSub Studio 当前支持两种运行方式。Portable RC 面向试用交付，源码开发版面向继续开发和本地调试。

### 方式 A：Portable RC

适用于 `dist/cinesub-portable-m6.7-rc1.zip` 或同结构的便携包：

1. 解压 zip 到当前用户有写入权限的目录。
2. 进入解压后的 `cinesub-portable/` 目录。
3. 双击或运行：

```text
start_app.bat
```

浏览器打开：

```text
http://127.0.0.1:7860
```

Portable RC 自带 `runtime/python/` 和 `tools/ffmpeg/`，不需要系统 Python，也不要求修改系统 PATH。首次运行会在解压目录内使用这些运行目录：

```text
input/
output/
work/
logs/
uploads/
models/
.cache/
```

`m6.7-rc1` 不包含 Whisper 模型、wheelhouse 或 CUDA 离线包。需要转写模型时按 Web 运行环境页面提示下载或导入；需要翻译时再配置 Provider。

外部试用前请先阅读 [TRIAL.md](TRIAL.md)，其中包含普通测试者的启动步骤、已知限制、反馈模板和 API key 安全提醒。

### 方式 B：源码开发版

适用于从 Git checkout 直接运行源码。建议使用 Windows 10/11、PowerShell，以及 Python 3.10-3.12。首次运行前确认项目目录可写，尤其是：

```text
.venv/
.cache/
models/
output/
work/
logs/
tools/
```

如果 PowerShell 阻止脚本运行，可以在当前窗口临时允许本次会话执行脚本：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 2. 安装源码依赖

源码开发版在项目目录运行：

```powershell
.\install.ps1
```

离线安装时，把 wheel 文件放入 `tools/wheelhouse/`，然后运行：

```powershell
.\install.ps1 -Offline
```

只有明确想重建虚拟环境时才使用：

```powershell
.\install.ps1 -Recreate
```

`install.ps1` 只创建或更新项目内 `.venv/`，不会修改系统 PATH、PowerShell profile、全局 Python 或全局 pip 缓存。

## 3. 启动源码 Web

```powershell
.\start_web.ps1
```

浏览器打开：

```text
http://127.0.0.1:7860
```

`start_web.ps1` 只使用 `.venv\Scripts\python.exe -B start_app.py`。如果启动失败，先看终端提示和：

```text
logs/web_server.log
```

### Portable RC 审计信息

`m6.7-rc1` 的便携包是 zip 形态，不是 PyInstaller EXE。包内 `release_manifest.json`、`release_report.md` 和 `release_checksums.sha256` 记录了文件数量、体积、最大依赖层、排除项和 leak scan 状态。zip 本身的 SHA256 写在包外的 `.sha256` sidecar，避免校验和循环。

## 4. 配置 Provider

在 Web 的“模型接口”区域配置翻译 Provider：

- API Base
- API Key
- 翻译模型
- active provider

Provider 未配置不会阻止 Web UI 启动；转写和本地质检仍可用。但实际翻译任务需要 Provider，缺少 API Key 或翻译模型时会失败。API Key 只应保存在 Provider 配置中，不要写入 Language Profile。

## 5. 放入 input/

把要批处理的视频或音频放入：

```text
input/
```

常见格式包括 `mp4`、`mkv`、`mov`、`avi`、`mp3`、`m4a`、`wav`。大文件建议直接放入 `input/` 或使用 Web 的本机路径方式，不要通过浏览器上传整部电影。

## 6. 扫描并开始处理

在 Web 的“流水线控制台”依次点击：

1. `扫描 input`
2. `开始处理 input`

同一时间只允许一个后台流水线任务运行。如果提示已有任务运行，等待完成后再重试，或查看“任务状态”和“操作日志”。

## 7. 查看状态和复核

处理过程中可以查看：

- `任务状态`：进度、失败任务、可下载产物
- `操作日志`：后台流水线日志尾部
- `异常复核`：质量报告和需要人工复核的字幕片段

`异常复核` 发现问题时，Web 可能显示 `issues_found`。这表示复核命令正常运行且发现质量问题，不等同于程序崩溃。

## 8. 下载字幕/报告

批处理默认输出：

```text
output/source/      原文 SRT
output/zh/          中文字幕 SRT
output/bilingual/   双语字幕 SRT
output/reports/     quality_report.json 和 review_needed.srt
```

Web 会在任务产物区域显示可下载链接。只有项目 `output/` 下存在且非空的产物可以通过 Web 下载；外部路径只显示为可复制路径。

## 9. 常见问题

### 缺少 .venv

运行：

```powershell
.\install.ps1
```

然后重新运行：

```powershell
.\start_web.ps1
```

### FFmpeg 缺失

Web 运行环境诊断会显示 FFmpeg 状态。安装内置 FFmpeg：

```powershell
.\scripts\download_ffmpeg.ps1
```

下载后 FFmpeg 位于 `tools/ffmpeg/bin/`，不会写入系统 PATH。

### Python 版本警告

推荐 Python 3.10-3.12。诊断显示 warning 时不一定阻止基础流程，但如果 faster-whisper 或 ctranslate2 安装失败，建议换用 Python 3.12 后重建 `.venv`。

### Provider 未配置

Web 可以启动，转写也可以运行；需要翻译时必须配置 active Provider、API Key 和翻译模型。

### 目录不可写

运行环境诊断会检查 `output/`、`work/`、`logs/` 是否可写。如果不可写，请把项目放到当前用户有写权限的位置，不要放到需要管理员权限的系统目录。

## 10. 已知限制

- 当前版本不做 PyInstaller 打包，也不提供 Docker/云端部署。
- 当前版本不实现 portable Python runtime 自动切换；`.venv` 仍是启动入口。
- 当前版本只稳定输出 SRT。ASS 参数是预留接口，不会生成 `.ass` 成品。
- 当前版本不做混合语言分段 ASR。混合语言影片会按一个主要源语言处理；必要时请拆分素材、使用更大的多语言 Whisper 模型，或改用官方源字幕翻译。
- 当前版本不做字幕编码自动检测。导入外部字幕前建议先转换为 UTF-8。

## M13 local launcher notes

Source checkout startup:

```powershell
.\start_web.ps1
```

Optional launcher modes:

```powershell
.\start_web.ps1 -NoBrowser
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
.\start_web.ps1 -NoBrowser -NonInteractive
```

Default URL: `http://127.0.0.1:7860/`.

`-Smoke` is a non-interactive startup readiness check. It does not open a browser, run ASR, translate, process media, load Whisper models, or download models.

If FFmpeg is missing, the Web UI can still open for settings and runtime diagnostics. Media jobs that need audio extraction will fail until FFmpeg is configured. Accepted variables are `CINESUB_FFMPEG` and `FFMPEG_PATH`; the project-local expected location is `tools/ffmpeg/bin/`.

M13 intentionally does not implement Electron, Tauri, a Windows installer, code signing, auto-update, a model hub, or dubbing/TTS features. See `docs/desktopization_readiness.md` for the desktop shell evaluation.
