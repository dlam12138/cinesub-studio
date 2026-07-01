# CineSub Studio

CineSub Studio 是一个本地影视字幕生产工具：把视频或音频转写为 SRT 字幕，可调用 OpenAI-compatible 翻译接口生成中文字幕或双语字幕，并带有基础质量检查。

它优先支持 CUDA 加速，适合长片和 `large-v3` 模型；没有 NVIDIA/CUDA 环境时也可以用 CPU 跑，只是速度会慢一些。

## 快速开始

首次使用：

```powershell
cd D:\Claude项目操作\电影翻译
.\install.ps1
.\start_web.ps1
```

打开浏览器访问：

```text
http://127.0.0.1:7860
```

把视频放到 `input/` 后，在 Web 的“流水线控制台”点击：

1. `扫描 input`
2. `开始处理 input`
3. 需要时查看 `操作日志`、`任务状态`、`异常复核`

生成结果默认在：

```text
output/source/      原文 SRT
output/zh/          中文字幕 SRT
output/bilingual/   双语字幕 SRT
output/reports/     质量报告和 review_needed.srt
```

## CUDA 优先

推荐设备策略是 `自动（CUDA 优先）`：

- CUDA 可用时使用 `cuda + float16`，适合 `medium`、`large-v3` 和长片。
- CUDA 不完整时自动回落 CPU，避免任务直接卡死。
- 明确选择 `NVIDIA CUDA` 时会严格检查环境；缺 DLL、驱动或依赖时会提示原因。

Web 顶部“运行环境”区域会显示：

- 当前 Python 和 `.venv` 状态
- faster-whisper / ctranslate2 是否可用
- FFmpeg 是否可用
- CUDA DLL、NVIDIA 驱动、推荐设备
- 已发现的本地 Whisper 模型
- 离线 wheelhouse 是否存在

## 内置环境

项目尽量把运行环境放在项目目录内，不要求修改系统 PATH：

```text
.venv/                    Python 虚拟环境
tools/python/             可选 portable Python
tools/wheelhouse/         可选离线 Python 依赖包
tools/ffmpeg/bin/         内置 FFmpeg
tools/cuda/               CUDA/cuDNN 运行时 DLL
models/                   Whisper 模型
.cache/                   pip / Hugging Face 缓存
```

这些目录都是运行产物，不需要提交到 Git。

当前版本要区分两件事：

- `.venv/` 是项目内虚拟环境。
- Python 本体可能来自系统 Python，也可能来自 `tools/python/python.exe`。

如果 Web 运行环境诊断显示 `python_source = project-venv-system-base`，表示现在用的是项目内 `.venv`，但 `.venv` 是由系统 Python 创建的。导入包含 `tools/python/` 的离线包后，可用 `.\install.ps1 -Recreate` 让 `.venv` 改用 portable Python。推荐 portable Python 版本锁定 3.12。

## 离线环境包

如果你已经有离线包，可以在 Web 的“运行环境”区域填写本机 zip 路径并点击“导入离线包”。离线包允许包含：

```text
tools/python/
tools/wheelhouse/
tools/ffmpeg/
tools/cuda/
models/
```

导入时会检查 zip 路径，防止解压到项目目录外。大包建议使用“本机路径导入”，不要走浏览器上传。

离线安装依赖：

```powershell
.\install.ps1 -Offline
```

如果 wheelhouse 不在默认位置：

```powershell
.\install.ps1 -Offline -Wheelhouse "D:\packages\wheelhouse"
```

## 一键下载环境

Web 的“运行环境”区域提供：

- `查看下载计划`：只显示 Python、wheelhouse、FFmpeg、CUDA、模型的大小、目标目录和用途，不会下载大文件。
- `下载 FFmpeg`：把 FFmpeg 下载到 `tools/ffmpeg/bin/`，不修改系统 PATH。

CUDA、portable Python、wheelhouse 和大型模型体积较大，建议通过离线包导入。后续可以把下载器封装成 `.exe`，但核心逻辑仍保留在项目 Python 脚本中。

## 单文件处理

命令行转写：

```powershell
.\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model small -Device auto
```

只用 CPU：

```powershell
.\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model small -Device cpu
```

强制 CUDA：

```powershell
.\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model large-v3 -Device cuda -ComputeType float16
```

## Provider 和语言配置

Web 的“模型接口”页用于配置翻译 API：

- API Base
- API Key
- 翻译模型
- active provider

Web 的“语言配置”页用于配置：

- 源语言和目标语言
- Whisper 模型、设备、精度
- 翻译风格
- 质检阈值

API Key 只属于 Provider，不要写进语言配置。

## 字幕格式

当前稳定输出仍是 SRT：

- 原文 SRT
- 中文字幕 SRT
- 双语 SRT
- 质检报告和 `review_needed.srt`

ASS 字幕已经预留了界面选项和后端参数，但当前版本不会生成 `.ass` 文件。勾选“ASS 输出接口预留”后，任务会记录请求并提示 `ASS output is reserved for a future version; no .ass file was generated.`，方便后续升级到样式化字幕。

## 失败恢复与完成跳过

批量流水线会保留任务状态和已生成产物，方便失败后恢复：

- “重试失败”只处理失败任务，不扫描 input 里的新文件。
- 已完成任务只有在最终字幕/报告等目标产物存在且非空时才会跳过。
- 失败任务重试时会复用已有且有效的音频、SRT、翻译结果和质量报告。
- 可能中断的运行中任务只显示 warning，不会被自动重置。

## Diagnostics API 稳定字段

诊断 API `GET /api/runtime/diagnostics` 会返回用户可读结构，其中 `ffmpeg_source`、`diagnostic_summary`、`diagnostic_items`、`status` 和 `blocking` 是稳定字段。比如 Python 版本不在推荐范围但基础流程可运行时，页面会显示 `warning`，这表示需要注意但不一定阻断使用。

## 常见问题

### CUDA 未就绪

先看 Web 的运行环境诊断。常见原因：

- 没有 NVIDIA 显卡或驱动
- `tools/cuda/` 缺少 `cublas64_12.dll` 或 `cudnn*_9.dll`
- 当前 Python 缺少 faster-whisper 或 ctranslate2

可以导入离线环境包，或临时把设备改为 `CPU`。

### FFmpeg 缺失

在 Web 点击“下载 FFmpeg”，或运行：

```powershell
.\scripts\download_ffmpeg.ps1
```

### 第一次模型加载失败

如果是首次运行，不要勾选“只使用本地已下载模型”。模型会下载到 `models/` 和 `.cache/huggingface/`。

### 离线运行

先导入包含 `tools/wheelhouse/`、`tools/ffmpeg/`、可选 `tools/cuda/` 和 `models/` 的离线包，再运行：

```powershell
.\install.ps1 -Offline
.\start_web.ps1
```

## 注意事项

- 不要把 API Key 发给别人。
- 不要手动删除 `output/` 成品字幕。
- 模型、CUDA、FFmpeg、wheelhouse、上传文件和缓存都可能很大，默认保留在项目目录。
- Web 服务只绑定 `127.0.0.1`，默认不暴露到局域网。
