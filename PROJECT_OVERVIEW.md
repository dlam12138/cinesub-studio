# CineSub Studio 项目总说明

> **English summary:** CineSub Studio is a local-first Windows workspace for
> long-form subtitle production. It combines audio extraction, local speech
> recognition, Chinese translation, quality checks, recovery, and
> source-language, Chinese, and bilingual SRT output. Generated subtitles
> should still be reviewed by a person before publication.

## 1. 项目定位

CineSub Studio 是一款面向 Windows 的本地长片字幕工作台，可自动完成音频提取、语音识别、中文翻译、质量检查以及原文、中文和双语 SRT 输出。

它主要服务于个人处理电影和长视频的真实需求：尽量减少环境配置和工具切换，让整部影片可靠完成，并把人工精力集中在系统标记出的疑难片段上。

## 2. 为什么开发

这个项目来自几个反复遇到的实际问题：

- 小语种和冷门电影经常缺少可用的中文字幕。
- 从视频到字幕通常需要在 FFmpeg、语音识别、翻译和字幕工具之间切换。
- Whisper、Python、FFmpeg、CUDA 和模型缓存的本地配置对普通使用并不友好。
- 长片处理耗时较长，程序中断后如果缺少可靠状态，很容易丢失进度或重复执行。
- 自动化应当处理可重复的工作，同时把低置信度、漏译和格式问题留给人工重点复核。

CineSub Studio 因此不是一个模型研究展示项目，而是一个希望经得起整部电影使用的个人工具。

## 3. 核心流程

```text
电影 / 视频
→ FFmpeg 提取音频
→ faster-whisper 本地识别
→ DeepSeek / OpenAI-compatible API 翻译
→ 字幕质量检查
→ 原文 / 中文 / 双语 SRT
```

语音识别在本机运行。只有启用翻译时，待翻译的字幕内容才会发送给用户配置的翻译 Provider。

## 4. 当前能力

- 提供 Windows Electron 便携运行方式。
- 便携包内置 Python、FFmpeg、FFprobe、CUDA 运行依赖和 `small` 模型。
- 默认使用 `small`，也支持用户显式准备和选择 `large-v3`。
- 支持自动语言检测、固定单语言和分块多语言识别。
- 支持单文件处理和目录批量任务。
- 支持任务历史、失败重试、运行状态恢复和 Web 重启后的 stale 状态识别。
- 可生成原文、中文和双语 SRT。
- 可生成质量警告、ASR review 信息和需要人工关注的时间位置。
- 日志、诊断和 Web API 对密钥及敏感路径进行限制和脱敏。
- 配置、模型、缓存、日志、中间状态和输出均保存在项目目录或便携包自己的 `data/` 中。
- 可以直接打开输出目录，并与 Subtitle Edit 等成熟字幕工具协作完成最终校对。

当前稳定成品格式是 SRT。项目保留少量兼容接口，但不把尚未稳定生成的格式描述为可交付能力。

## 5. 项目边界

CineSub Studio 负责把长片字幕流程自动化，但不承诺自动结果达到人工专业字幕质量。

- ASR 可能出现错识别、漏识别、断句不合理或语言判断错误。
- 翻译可能出现错译、漏译、语气偏差和人物称谓不一致。
- 背景音乐、低声对白、口音、噪声和多人重叠说话会降低识别效果。
- `large-v3` 运行更慢，也不保证在所有素材上都比 `small` 更准确。
- selective retry 当前保持 `dry_run`，不会默认改写识别文本。
- `three_pass` 需要更多 API 调用，现有证据没有证明它普遍优于默认翻译策略。
- 最终字幕建议使用字幕编辑器结合原片人工复核。
- 项目不计划内置完整波形编辑器或复刻全功能字幕编辑器。
- 项目不追求支持所有操作系统、ASR 后端、模型组合或字幕格式。

## 6. v0.7.0 当前状态

```text
当前稳定节点：v0.7.0
版本定位：个人自用可靠性版本
```

v0.7.0 的重点是长片任务可靠性、恢复、诊断、隐私边界和实用输出，而不是宣传模型质量提升。

- 长片 Worker 生命周期和正式可靠性验收通过。
- 一部真实长片完成抽音频、ASR、翻译、质检和三类字幕输出。
- 便携包在全新中文及空格路径中完成启动和短媒体 smoke。
- 中断后的 stale 状态、显式恢复和重复 Worker 防护通过验证。
- Stage 3 质量研究已经收口，没有候选满足既定晋级条件。
- 生产 ASR 模型分工、retry 和默认翻译策略没有因研究结果而改变。
- 本版本不宣称识别准确率或翻译质量获得显著提升。

`v0.7.0` Tag 是当前稳定源码节点。公开便携包是否已经发布及其校验文件，请始终以 [GitHub Releases](https://github.com/dlam12138/cinesub-studio/releases) 页面为准。

## 7. 技术架构

```text
Electron
└─ 本地 Web UI（127.0.0.1）
   └─ Python Pipeline
      ├─ FFmpeg / FFprobe
      ├─ faster-whisper
      ├─ DeepSeek / OpenAI-compatible API
      ├─ Subtitle QA
      └─ SRT outputs
```

Electron 只负责启动本地后端、显示界面和退出时清理进程。Python Pipeline 负责媒体处理、任务状态、恢复、翻译和字幕产物。所有运行数据都留在当前源码目录或 EXE 同级 `data/`，不依赖系统 Python 或全局模型缓存。

## 8. 怎样开始使用

1. 从 GitHub Releases 获取便携 ZIP 和对应 SHA256 文件。
2. 校验哈希并完整解压，不要直接在压缩软件中运行。
3. 双击 `CineSubStudio.exe`。
4. 如需中文字幕，在“模型接口”中配置 DeepSeek 或其他 OpenAI-compatible Provider。
5. 选择视频或批量输入目录，再选择模型、语言模式和质量预设。
6. 启动处理，在任务界面查看阶段、日志和恢复提示。
7. 完成后打开输出目录，结合原片复核原文、中文或双语 SRT。

只做本地语音识别时不需要 API Key。更详细的操作说明见：

- [README：下载、配置与故障排查](README.md)
- [Windows 便携版快速入门](docs/windows_portable_quickstart.md)
- [用户可见版本变化](CHANGELOG.md)

## 9. 后续方向

项目以后采用路线 A：以个人真实使用为驱动，优先修复阻碍整部电影处理的问题。

后续工作的优先级是：

1. 可靠完成整部电影。
2. 减少本地环境配置。
3. 提供可恢复任务和清晰、可执行的诊断。
4. 稳定生成实用的中文和双语 SRT。
5. 减少人工复核范围，但不省略最终人工判断。

项目不再主动扩展大规模模型研究、语料 Campaign、多 ASR 后端平台或完整字幕编辑能力。只有真实使用中明确影响整片完成、输出可用性、隐私或恢复的问题，才优先进入产品改进。
