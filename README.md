# 智译字幕工坊 / CineSub Studio

CineSub Studio 是一款面向 Windows 的本地长片字幕工作台，用于自动完成语音识别、中文翻译、质量检查以及原文、中文和双语 SRT 输出。

当前稳定源码节点为 [`v0.7.0`](https://github.com/dlam12138/cinesub-studio/tree/v0.7.0)。自动生成的字幕仍应在公开使用前人工复核；最新公开便携包以 [GitHub Releases](https://github.com/dlam12138/cinesub-studio/releases) 页面为准。

[项目总说明](PROJECT_OVERVIEW.md) · [版本变化](CHANGELOG.md) · [Windows 便携版快速入门](docs/windows_portable_quickstart.md)

## 下载与启动

普通用户只需要从 [GitHub Release v0.6.2](https://github.com/dlam12138/cinesub-studio/releases/tag/v0.6.2) 下载：

```text
CineSubStudio-0.6.2-windows-x64-portable.zip
CineSubStudio-0.6.2-windows-x64-portable.zip.sha256
```

1. 核对 ZIP 的 SHA256。
2. 把 ZIP 完整解压到当前用户有写权限的目录。
3. 双击解压目录中的 `CineSubStudio.exe`。

PowerShell 校验示例：

```powershell
Get-FileHash .\CineSubStudio-0.6.2-windows-x64-portable.zip -Algorithm SHA256
Get-Content .\CineSubStudio-0.6.2-windows-x64-portable.zip.sha256
```

不要直接在压缩软件中运行 EXE。应用未进行代码签名，Windows 可能显示来源或 SmartScreen 提示。

## 便携版包含什么

ZIP 已内置：

- Electron 桌面程序 `CineSubStudio.exe`
- portable Python 与运行依赖
- FFmpeg、FFprobe
- faster-whisper `small` 模型
- CUDA 运行库

无需安装系统 Python、FFmpeg 或 CUDA Toolkit，也不会修改系统 PATH。CUDA 运行库不包含 NVIDIA 驱动；驱动或显卡不兼容时自动回退 CPU。`large-v3` 不随包提供。

应用只绑定 `127.0.0.1`。配置、API Key、缓存、模型、日志和字幕产物都写入 EXE 同级 `data/`：

```text
data/config/    Provider 和 Language Profile
data/input/     批量输入
data/output/    字幕与报告
data/work/      处理中间产物
data/logs/      运行日志
data/uploads/   单文件任务副本
data/models/    本地 ASR 模型
data/.cache/    Electron、pip 和 Hugging Face 缓存
```

移动或备份应用时，请移动整个解压目录。

## 使用流程

### 单文件处理

1. 打开“单文件处理”。
2. 选择本机视频或音频路径。
3. 选择语音识别方式。
4. 按需启用翻译并选择语言风格。
5. 开始处理，在右侧状态区查看日志并下载字幕。

### 批量处理

1. 把媒体放入 `data/input/`，或在批量页面选择输入目录。
2. 配置识别方式和翻译设置。
3. 开始处理。
4. 在 `data/output/` 查看原文、译文、双语字幕和质量报告。

大文件建议使用本机路径或输入目录，不要通过浏览器上传整部电影。
Web/Electron 批量任务不会移动或删除所选输入文件；归档移动只保留为开发者
显式 CLI 选项。

## 三种 ASR 模式

- `自动检测（默认）`：整片自动检测并转写，适合大多数单语言视频。
- `固定单语言`：必须选择英语、法语、中文等具体语言，整片按指定语言转写。
- `多语言`：按语音停顿分块，每块独立检测语言并转写，再恢复原时间轴并去除边界重复。

三种模式都只使用 faster-whisper。默认旧调用仍只把置信度和异常检测用于日志、报告及“建议人工听取”标记，不会自动换模型。v0.7 的“识别质量闭环”可选启用词级时间戳、确定性重切分和固定配方局部重试；真实媒体验收尚未积累足够的自动替换证据，因此 `balanced` 和 `quality` 预设都只执行 dry-run 并写入 ASR 审计报告。只有显式请求 `apply` 时，才会在预算、硬拒绝和事务校验全部通过后局部替换 suspicious cue。

## 翻译 Provider

转写和本地质检不需要 API Key。需要翻译时，在“模型接口”中配置：

- API Base
- API Key
- 翻译模型
- 质量模型（可选）
- 默认 Provider

API Key 只保存在本地 `data/config/`，不应写入 Language Profile、字幕或日志。

当前前端不提供新增或修改翻译提示词的入口。已有 Profile 中的 `translation_style` 继续生效，后端 CLI/API 的 `translation_prompt` 也继续兼容；术语表仍可在前端编辑。

## 输出与复核

默认输出包括：

```text
data/output/source/      原文 SRT
data/output/zh/          中文字幕 SRT
data/output/bilingual/   双语字幕 SRT
data/output/reports/     质量报告与 review_needed.srt
```

质量告警表示字幕建议人工复核，不等同于程序崩溃。当前稳定输出格式为 SRT；ASS 字段属于预留接口，不会生成 `.ass` 成品。

任务结束后可在结果区使用“打开输出目录”，也可以把 SRT 交给 Subtitle Edit
等成熟字幕工具做最终校对。CineSub Studio 不内置完整字幕编辑器。

## 失败恢复

- Web 或窗口意外关闭后，重新启动应用并查看原任务状态。
- 已由后台 Worker 接管且身份一致的任务不会再启动第二个 Worker。
- `failed` 任务可使用失败重试；`stale/running-after-crash` 只显示警告，不会自动重置。
- 只有配置签名一致且最终产物存在、非空的已完成任务才会跳过。
- 恢复前不要移动输入文件或手工改写 `data/work/` 中的运行记录。

## 已知限制

- 自动字幕可能错识别、漏识别，背景音乐、重叠说话和低音量会降低效果。
- 翻译可能错译、漏译或出现称谓不一致。
- `large-v3` 更慢，并不保证对所有素材都比 `small` 准确。
- selective retry 保持 `dry_run`，不会默认替换识别文本。
- `three_pass` 调用次数更多，现有证据未证明它优于默认 `standard`。
- 自动结果不等同于专业字幕，建议使用字幕编辑器完成最终复核。

## 常见问题

### 程序无法启动

- 确认 ZIP 已完整解压。
- 确认程序目录可写。
- 查看 `data/logs/`。
- Windows 来源提示不代表安装失败；只应使用本项目 Release 并核对 SHA256。

### 识别使用 CPU

CUDA 包不包含显卡驱动。确认 NVIDIA 驱动可用后查看“运行环境”诊断；环境不兼容时 CPU 回退属于预期行为。

### 未检测到语音

确认媒体包含可听语音且 FFmpeg 诊断正常。没有有效语音时任务会明确失败，不会生成空白成功产物。

### 无法翻译

确认已设置默认 Provider、API Key 和翻译模型。Provider 未配置不会影响本地转写。

## 源码开发

GitHub 源码面向开发和本地调试，不包含模型、Python 运行时、FFmpeg、CUDA、用户配置或媒体。

```powershell
.\install.ps1
.\start_web.ps1
```

非交互启动检查：

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
```

开发要求、目录边界、API 约束、测试命令和发布规则见 [`AGENTS.md`](AGENTS.md)。Electron 子项目说明见 [`desktop/README.md`](desktop/README.md)。

开发者资料：

- [模型参数与调优参考](docs/model_tuning_reference.md)

0.7.0 便携包的唯一构建入口：

```powershell
.\.venv\Scripts\python.exe -B scripts\build_portable_release.py
```

本版本只发布 Electron 免安装 ZIP 和对应 SHA256，不提供安装器、自动更新或代码签名。
