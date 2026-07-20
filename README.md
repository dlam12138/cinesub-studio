# 智译字幕工坊 / CineSub Studio

智译字幕工坊 / CineSub Studio 是一个 Windows 本地影视字幕工具：把视频或音频转写为 SRT 字幕，可调用 OpenAI-compatible 翻译接口生成中文字幕或双语字幕，并输出基础质量报告和 `review_needed.srt`。

当前版本面向本机使用。它不要求修改系统 PATH，也不要求安装前端构建工具、npm、CDN 或浏览器插件。

v0.6.2 Windows 版只提供 Electron 免安装 ZIP，不提供安装器。压缩包自带 `CineSubStudio.exe`、portable Python、运行依赖、FFmpeg、CUDA 运行库和 faster-whisper `small` 模型；设备默认使用 `auto`，检测到兼容 NVIDIA 驱动时使用 GPU，否则自动回退 CPU。正常离线转写不会下载模型。

## 1. 选择运行方式

智译字幕工坊 / CineSub Studio 当前支持便携版和源码开发版两种运行方式。便携版面向普通用户，源码开发版面向继续开发和本地调试。

### 方式 A：0.6.2 便携版

适用于 `CineSubStudio-0.6.2-windows-x64-portable.zip`：

1. 把 ZIP 完整解压到当前用户有写入权限的目录，不要直接在压缩软件内运行。
2. 进入解压后的 `CineSubStudio-0.6.2-windows-x64-portable/` 目录。
3. 双击：

```text
CineSubStudio.exe
```

应用会打开本地 Electron 窗口并在后台启动仅绑定 `127.0.0.1` 的服务。便携版自带 Python、FFmpeg、CUDA 运行库和 `data/models/` 下的 `small` 模型，不需要系统 Python、FFmpeg 或 CUDA Toolkit，也不修改系统 PATH。CUDA 运行库不包含 NVIDIA 显卡驱动；没有兼容 NVIDIA 环境时自动使用 CPU。运行数据只写入 EXE 同级 `data/`：

```text
data/input/
data/output/
data/work/
data/logs/
data/uploads/
data/models/
data/config/
data/.cache/
```

`small` 模型支持自动检测、固定单语言和多语言三种 ASR 模式的离线转写。`large-v3` 不随包提供，如有需要可稍后导入 `data/models/`。翻译仍需用户自行配置 Provider。移动便携版时应移动整个解压目录。

外部试用前请先阅读 [TRIAL.md](TRIAL.md)，其中包含普通测试者的启动步骤、已知限制、反馈模板和 API key 安全提醒。

应用未进行代码签名，Windows 可能显示来源或 SmartScreen 提示。请只从本项目 GitHub Release 下载，并用同名 `.sha256` 文件核对压缩包。

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

### 便携版审计信息

0.6.2 的交付物只有 Electron 免安装 ZIP 和对应 `.sha256` 文件，不生成 NSIS、PyInstaller 或其他安装器。包内 `release_manifest.json` 和 `release_checksums.sha256` 记录运行时组成与文件校验值；ZIP 自身的 SHA256 写在包外 sidecar 中。

若包含完整 CUDA 的 ZIP 达到 GitHub 单文件 2 GiB 限制，Release 会提供 CPU 可直接运行的主 ZIP，以及单独的 `windows-x64-cuda-addon.zip`。把 CUDA add-on 解压到主程序目录即可补齐 `resources/app/tools/cuda/`。

0.6.2 的活动发布入口冻结为：

```powershell
.\.venv\Scripts\python.exe -B scripts\build_portable_release.py
```

旧脚本型 portable 和 NSIS 构建入口已经退役；后续修改发布布局必须升级版本。

## 4. 配置 Provider

在 Web 的“模型接口”区域配置翻译 Provider：

- API Base
- API Key
- 翻译模型
- 质量模型（可选；三步翻译的反思和终稿使用，留空则复用翻译模型）
- active provider

Provider 未配置不会阻止 Web UI 启动；转写和本地质检仍可用。但实际翻译任务需要 Provider，缺少 API Key 或翻译模型时会失败。API Key 只应保存在 Provider 配置中，不要写入 Language Profile。

### 翻译提示词的前端冻结

当前版本的单文件页面和 Language Profile 编辑页不提供新增或修改翻译提示词的输入框。已有 Profile 中的 `translation_style` 仍会生效，后端 CLI/API 的 `--translation-prompt` / `translation_prompt` 字段也继续支持，自动化调用和现有本地配置不会失效。这只是 UI 冻结，并未删除后端提示词能力。术语表仍可在前端编辑。

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

单文件和批量处理都提供相同的三种 ASR 模式：

- `自动检测（默认）`：整片交给 faster-whisper 自动检测并转写，适合大多数单语言视频。
- `固定单语言`：必须指定英语、法语、中文等具体语言，整片按该语言转写。
- `多语言`：VAD 按语音停顿组合约 45 秒、最长 60 秒的语音块，每块独立检测语言并转写，再恢复原始时间轴、去除边界重复并合并。

CLI 对应参数为 `--asr-mode auto|fixed|multilingual` 和 `--language`。为兼容旧调用，只传具体 `--language` 时会推断为 `fixed`；都不传时使用 `auto`。`fixed` 缺少语言会拒绝启动，`auto` 和 `multilingual` 不接受固定语言。

批量工作区默认使用中文控制台：任务行只保留状态、阶段、进度与关键告警，点击任务或按 Enter 可打开详情。桌面端使用右侧详情抽屉，窄屏使用全屏详情层；已生成的 SRT 可在详情中分页只读预览。当前界面统一为中文，不显示不可用的语言切换入口。

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

### 高质量模式（显式可选）

- 翻译可选择 `three_pass`：快模型初译、质量模型反思、质量模型终稿。通常约为标准模式三倍调用量，阶段缓存允许失败后续跑，最终字幕只在全部结构与可靠性检查通过后原子写入。
- 翻译可显式选择实验策略 `semantic_review`、`wenyi_review` 和 `semantic_wenyi_review`。组合模式以 Semantic 成品为基线，由 WenYi 只提出挑战候选，并要求八项高置信匿名证明后才采用。
- 三部 451 条终选中三种策略均未达到严重错误为 0 的晋级门槛；`semantic_review` 仅作为三者中的相对推荐，`wenyi_review` 与组合模式标记为未通过。默认仍为 `standard`，详见 `docs/translation_strategy_finale.md`。
- Language Profile 可配置 `asr_mode`、固定源语言、faster-whisper 性能参数和 `translation_strategy`。旧 Profile 的具体 `source_language` 会自动按 `fixed` 读取，`auto` 会按 `auto` 读取；重新保存时写入 `asr_mode`。
- 授权翻译金标与匿名 A/B 工具说明见 `tests/translation_benchmark/README.md`。自动指标只作证据，三步模式晋升仍要求非平局偏好率至少 60%，且各素材类别无净退化。

### OCR / 历史 ASR 研究资产

如果视频带有硬字幕，可使用独立 CLI 把 OCR 双语字幕与 ASR/译文进行离线对照，生成差异报告和候选初筛。该流程默认不访问网络、不覆盖字幕，也不会把 OCR 当成人工金标。FunASR、WhisperX、ASR candidate、`mixed-route-v1` 和 segment routing 已退出产品链路；一次性可执行脚本及专属测试已删除。历史验收记录、研究说明与依赖清单仅作为离线资料保留，不进入便携包，也不会参与正常任务。研究资产说明见 [`research/README.md`](research/README.md)，OCR 结果解释见 [`docs/ocr_weak_evidence_evaluation.md`](docs/ocr_weak_evidence_evaluation.md)。

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
- 当前产品 ASR 只使用 faster-whisper，并仅提供自动检测、固定单语言和多语言三种模式；离线研究工具不会自动路由或替换产品输出。
- 当前版本不做字幕编码自动检测。导入外部字幕前建议先转换为 UTF-8。

## M13 历史启动器说明

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

以下内容记录 Electron 便携版引入前的历史启动方式，仅用于维护参考；当前 0.6.2 已使用 Electron 免安装壳。项目仍不提供安装器、代码签名、自动更新、模型中心或配音/TTS。桌面化评估见 `docs/desktopization_readiness.md`。
