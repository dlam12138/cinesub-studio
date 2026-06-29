# CineSub Studio

CineSub Studio 是一个影视字幕自动化生产系统。使用 `faster-whisper` 进行语音识别生成 SRT 字幕，通过 LLM API 进行字幕翻译，内置自动质检，支持批量流水线处理。

## 核心能力

| 能力 | 说明 |
|------|------|
| **语音转写** | faster-whisper（支持 tiny ~ large-v3），自动语言识别 |
| **字幕翻译** | 通过 LLM API（OpenAI 兼容 / Anthropic），支持双语或纯译文输出 |
| **自动质检** | SRT 格式检查 + 翻译质量检查 + 异常片段标记 |
| **批量流水线** | 自动发现视频 → 提取音频 → 转写 → 翻译 → 质检 → 输出 |
| **断点续跑** | 各阶段独立缓存，中断后不从头开始 |
| **语言策略路由** | 主流语言 vs 小语种自动切换翻译策略 |
| **Web UI** | 本地 Web 界面用于单文件手动处理 |

## 两种使用方式

### 方式一：批量生产流水线（推荐）

把视频丢进 `input/` 目录，系统自动处理：

```powershell
# 完整流水线
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --input input --model large-v3 --device cuda `
  --api-base "https://api.deepseek.com/v1" `
  --api-key "sk-xxx" `
  --llm-model "deepseek-chat"

# 仅扫描（不处理）
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --scan

# 查看任务状态
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --status

# 重试失败任务
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --retry-failed
```

**流水线架构：**

```text
input/                          ← 丢视频进来
   ↓ 自动发现
work/                           ← 中间音频、状态文件、翻译缓存
   ↓ 提取音频 (ffmpeg)
   ↓ 语音识别 (Whisper)         → 保存语言检测 JSON
   ↓ LLM 翻译                   → 根据语言自动选择策略
   ↓ 自动质检                   → 生成质量报告 + review_needed.srt
output/
  source/       ← 原文字幕 SRT
  zh/           ← 中文字幕 SRT（translated 模式）
  bilingual/    ← 双语字幕 SRT
  reports/      ← 质量报告 JSON
archive/        ← 已完成视频
failed/         ← 失败任务
```

### 方式二：Web 流水线控制台（推荐）

启动 Web 服务后，打开 `http://127.0.0.1:7860` 使用流水线控制台：

```powershell
.\start_web.ps1
# 打开 http://127.0.0.1:7860
```

`start_web.ps1` 是 Windows 便利入口；实际启动、等待服务就绪、打开浏览器和退出清理都由 `start_app.py` 负责。也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -B start_app.py
```

控制台提供两个标签页：

| 标签 | 功能 |
|------|------|
| **⚙ 流水线控制台** | 扫描 input、开始处理、查看状态、异常复核、重试失败、操作日志 |
| **📝 单文件处理** | 上传视频、手动转写 + 翻译（原有功能） |

流水线控制台操作按钮：

| 按钮 | 说明 | 类型 |
|------|------|------|
| **扫描 input** | 显示 `input/` 目录中待处理的文件列表 | 只读 |
| **查看状态** | 显示所有任务的进度（阶段、状态、重试次数） | 只读 |
| **异常复核** | 汇总所有质检报告中的问题，只显示需要人工关注的片段 | 只读 |
| **开始处理 input** | 后台启动完整流水线：扫描 → 转写 → 翻译 → 质检 → 归档 | **会修改文件** |
| **重试失败** | 仅重试之前失败的任务，**不扫描新文件**，不处理已成功的任务 | **会修改文件** |
| **刷新全部** | 同时刷新扫描、状态、复核（只读操作） | 只读 |

> ⚠️ `开始处理 input` 和 `重试失败` 互斥：同一时间只允许一个后台流水线任务运行。重复点击会返回 HTTP 409 并提示"已有流水线任务正在运行"。

> ⚠️ `重试失败` 仅重置状态为 `failed` 的任务并重新处理，**不会**扫描 `input/` 目录中的新文件。如需处理新文件，请用 `开始处理 input`。

控制台后端 API：

```text
GET  /api/pipeline/scan            扫描 input 目录（只读）
GET  /api/pipeline/status          查看所有任务状态（只读）
GET  /api/pipeline/review          查看异常复核摘要（只读）
GET  /api/pipeline/logs            查看流水线操作日志（只读）
GET  /api/pipeline/task            查看后台任务运行状态（只读）
POST /api/pipeline/run             启动完整流水线（后台，返回 202 / 冲突返回 409）
POST /api/pipeline/retry-failed    仅重试失败任务（后台，返回 202 / 冲突返回 409）
```

后台任务日志写入 `logs/pipeline.log`，前端通过轮询 `/api/pipeline/logs` 和 `/api/pipeline/status` 查看进度。

## 模型接口配置

Web 控制台的第三个标签页"🔑 模型接口"用于管理本地 Provider 配置，避免每次手动输入 API 参数。

### 配置文件

Provider 配置保存在 `config/providers.local.json`，首次使用 Web 控制台新增接口时自动创建。

> ⚠️ 此文件包含 API Key，已加入 `.gitignore`，**不要提交到仓库**。

配置结构：

```json
{
  "version": 1,
  "active": "openai-main",
  "providers": [
    {
      "id": "openai-main",
      "name": "OpenAI 主接口",
      "protocol": "openai-compatible",
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "chat_model": "gpt-4o",
      "translation_model": "gpt-4o",
      "whisper_model": "large-v3",
      "whisper_device": "cuda",
      "enabled": true,
      "notes": ""
    }
  ]
}
```

### 操作说明

| 操作 | 说明 |
|------|------|
| 新增接口 | 填写 Provider ID、名称、API Base、Key、模型等，保存到本地 |
| 设为默认 | 将该 Provider 设为 active，Pipeline 和单文件处理默认使用 |
| 编辑 | 修改已有配置（API Key 留空则保留旧值） |
| 测试连接 | 发送极短请求验证 API 可用性，返回延迟和模型信息 |
| 删除 | 删除 Provider，若为 active 则自动清空默认 |

### 安全措施

- 列表和详情 API 返回 `api_key_masked`（如 `sk-...abcd`），不返回完整 Key
- 后端日志不出现在何 API Key
- 配置文件已 `.gitignore`

### Pipeline 使用 Provider

- `POST /api/pipeline/run` 和 `POST /api/pipeline/retry-failed` 默认使用 active provider
- 命令行：`.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --provider <id>` 使用指定 Provider
- CLI 显式参数优先于 Provider 配置：`--api-key` > Provider > 默认值

### Provider API

```text
GET    /api/providers                 列表（脱敏）
GET    /api/providers/active          当前默认（脱敏）
POST   /api/providers                 新增
PUT    /api/providers/<id>            更新
DELETE /api/providers/<id>            删除
POST   /api/providers/<id>/activate   设为默认
POST   /api/providers/<id>/test       测试连接
```

## 语言配置 Language Profile

Web 控制台的第四个标签页"🌐 语言配置"用于管理不同语言/影片类型的转写、翻译、质检策略。

### Language Profile vs Provider

| 职责 | Provider | Language Profile |
|------|----------|-----------------|
| API Key / API Base / LLM 模型 | ✅ | ❌ 不包含 |
| 源语言 / 目标语言 | ❌ | ✅ |
| Whisper 模型 / 设备 / VAD | ✅ 可设 | ✅ 优先 |
| 质检阈值 | ❌ | ✅ |
| 翻译风格 prompt | ❌ | ✅ |
| 原文校对 / 译文润色开关 | ❌ | ✅ |

### 默认内置 Profile

| ID | 名称 | 用途 |
|----|------|------|
| `auto-detect` | 自动识别语言 | 未知语言影片默认模式，Whisper 自动检测 |
| `fr-film` | 法语电影 | 强制法语识别，启用校对和润色 |
| `generic-european-film` | 欧洲语种通用 | 西/意/德/葡/荷/瑞/波/捷等欧洲语种 |

内置 profile 在配置文件缺失时始终可用。本地配置可覆盖或新增。

### 配置文件

保存在 `config/language_profiles.local.json`（已 gitignore），结构参见 `config/language_profiles.local.json.example`。

### 优先级

```
CLI 显式参数 > Language Profile > Provider > 默认值
```

API Key / LLM 模型：Provider 优先。
语言 / ASR / VAD / 质检阈值 / prompt：Language Profile 优先。

### CLI 使用

```powershell
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --input input --provider openai-main --language-profile fr-film
```

### Language Profile API

```text
GET    /api/language-profiles                 列表
GET    /api/language-profiles/active          当前默认
POST   /api/language-profiles                 新增
PUT    /api/language-profiles/<id>            更新
DELETE /api/language-profiles/<id>            删除
POST   /api/language-profiles/<id>/activate   设为默认
```

### 方式三：CLI 单文件处理

```powershell
.\run_transcribe.ps1 -InputFile "movie.mp4" -Model large-v3 -Device cuda
```

## 目录结构

```text
电影翻译/
├── input/                       # 批量输入（放视频到这里）
├── output/
│   ├── source/                  # 原文字幕
│   ├── zh/                      # 中文字幕
│   ├── bilingual/               # 双语字幕
│   └── reports/                 # 质检报告 + review_needed.srt
├── work/
│   ├── states/                  # 任务状态 JSON（断点续跑）
│   └── translation-cache/       # 翻译缓存
├── archive/                     # 已完成视频
├── failed/                      # 失败任务
│
├── src/
│   ├── pipeline/batch_worker.py # 批量流水线引擎
│   ├── core/quality_checker.py  # 自动质检模块
│   ├── core/transcribe.py       # 转写 + 语言识别
│   ├── core/subtitle_translate.py # 翻译模块
│   ├── web/web_server.py        # Web 后端
│   └── tools/                   # 下载、分析、辅助工具
├── web/index.html               # Web 前端
│
├── install.ps1                  # 安装脚本
├── start_web.ps1                # 启动 Web UI
└── run_transcribe.ps1           # CLI 转写
```

## 自动质检

质检自动检查以下问题并输出质量报告：

| 检查项 | 严重度 | 说明 |
|--------|--------|------|
| `broken_numbering` | error | SRT 编号不连续 |
| `broken_timestamp` | error | 时间码格式异常 |
| `llm_boilerplate` | error | 翻译结果中混入 LLM 废话 |
| `time_overlap` | warning | 相邻字幕时间轴重叠 |
| `empty_subtitle` | warning | 空字幕 |
| `too_long` | warning | 单条字幕过长 |
| `possibly_untranslated` | warning | 可能未翻译的片段 |
| `duplicate_content` | warning | 连续重复字幕 |
| `mixed_language` | warning | 中英文混乱 |
| `too_short_duration` | info | 显示时长过短 |
| `count_mismatch` | error | 原文译文条数不一致 |

质检输出：
- `质量报告.json` — 完整结构化报告
- `review_needed.srt` — 仅包含异常片段，人工只需看这些

### 独立运行质检

```powershell
.\.venv\Scripts\python.exe -B src\core\quality_checker.py output/source/movie.srt --translated output/bilingual/movie.bilingual.srt
```

## 语言识别与策略路由

Whisper 自动检测语言，结果保存为 `.lang.json` 文件。系统根据检测结果自动选择翻译策略：

```text
日语/韩语/英语/法语等主流语言  →  常规影视翻译提示词
小语种（不在主流列表）        →  保守翻译提示词（保留更多原文）
语言识别置信度 < 70%          →  额外标注 [待确认]
```

## 翻译 API 配置

支持 OpenAI 兼容 API 和 Anthropic Claude API。

```powershell
# 通过环境变量设置（推荐）
$env:SUBTITLE_LLM_API_KEY = "your-api-key"

# 或在命令行直接传递
.\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --input input --api-key "sk-xxx" --api-base "https://api.openai.com/v1" --llm-model "gpt-4o"
```

## 安装

```powershell
cd D:\Claude项目操作\电影翻译
.\install.ps1
```

`install.ps1` 只负责创建虚拟环境和安装 Python 依赖，不要求系统 PATH 中已有 FFmpeg。

## 内置 FFmpeg

项目优先使用内置 FFmpeg，不要求用户提前配置系统 PATH。

Windows 用户可以运行：

```powershell
.\scripts\download_ffmpeg.ps1
```

安装后 FFmpeg 会放在：

```text
tools/ffmpeg/bin/ffmpeg.exe
tools/ffmpeg/bin/ffprobe.exe
```

查找优先级：

```text
CINESUB_FFMPEG / FFMPEG_PATH
→ 项目内 tools/ffmpeg/bin/
→ 项目内 tools/
→ bin/
→ vendor/ffmpeg/
→ 系统 PATH
```

重新下载：

```powershell
.\scripts\download_ffmpeg.ps1 -Force
```

本脚本不会修改系统 PATH，也不需要管理员权限。

## PowerShell 入口

项目核心逻辑由 Python 承担，PowerShell 只作为 Windows 便利入口：

```powershell
.\start_web.ps1          # thin wrapper for .\.venv\Scripts\python.exe -B start_app.py
.\run_transcribe.ps1     # thin CLI convenience wrapper
.\analyze_subtitles.ps1  # thin wrapper for src/tools/analyze_subtitles_workflow.py
```

推荐启动方式仍然是：

```powershell
.\.venv\Scripts\python.exe -B start_app.py
```

这些 `.ps1` 不负责后台调度、日志解析、状态判断、字幕处理或 JSON 读写；遇到这类需求时应优先扩展 Python 模块或 CLI。

## 自测

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\core\subtitle_translate.py --self-test
.\.venv\Scripts\python.exe -B src\core\quality_checker.py --self-test
.\.venv\Scripts\python.exe -B -c "import transcribe, web_server, download_model_file, subtitle_translate, quality_checker, batch_worker; print('imports ok')"
```

## 真实短片验收

用于验证 1-3 分钟真实短片的端到端效果，不提交视频、不提交 API Key。

1. 把样本放入对应目录：

```text
tests/e2e_samples/fr_short/sample.mp4
tests/e2e_samples/auto_unknown_short/sample.mp4
tests/e2e_samples/european_short/sample.mp4
```

2. 按需复制并修改样本配置：

```powershell
Copy-Item tests/e2e_samples/samples.example.json tests/e2e_samples/samples.local.json
```

`samples.local.json` 已忽略，不会提交。每个样本只记录 `file`、`language_profile`、`provider`、`expected_language` 和人工备注。

3. 运行验收：

```powershell
.\.venv\Scripts\python.exe -B src\pipeline\e2e_runner.py --config tests/e2e_samples/samples.local.json
```

运行时脚本会把样本映射到 `work/e2e_samples/<sample_id>/<sample_id>.<ext>` 后再调用流水线，优先使用硬链接，失败时才复制。这样即使多个目录里都叫 `sample.mp4`，输出的 SRT 和报告也会按样本 ID 区分，不会互相覆盖；原始样本不会被移动。

脚本运行前会做 preflight 检查：样本文件是否存在、Language Profile 是否存在、Provider 是否存在且启用、Provider 是否配置了 API Base / 翻译模型 / API Key。检查结果会写入报告；如果真实样本存在但配置有 error，脚本不会启动流水线，避免白跑。

没有真实视频时也可以直接跑示例配置，脚本会把缺失样本标记为 `missing_sample`，不会崩溃：

```powershell
.\.venv\Scripts\python.exe -B src\pipeline\e2e_runner.py --config tests/e2e_samples/samples.example.json
```

4. 查看报告：

```text
reports/e2e_sample_report.json
reports/e2e_sample_report.md
```

报告会汇总 `detected_language`、`language_probability`、`forced_language`、source/zh/bilingual 字幕条数、质检 error/warning 数量、`review_needed.srt` 条数和人工观感。报告只写 Provider ID，不读取或输出 API Key。

跑完真实样本后，可以复制 `tests/e2e_samples/manual_review.template.md` 记录人工观感。重点不是长篇校对，而是判断问题归属：ASR、翻译、质检阈值，还是配置错误。

判断问题归属：

- 识别语言错误、置信度低、source 字幕不可读：优先看 ASR / Language Profile。
- zh 字幕生硬、漏译、误译：优先看翻译模型、提示词和 batch 大小。
- bilingual 条数不一致或时间轴异常：优先看翻译输出格式和质检报告。
- `review_needed_count` 过多但问题不严重：优先调整 Language Profile 质检阈值。

## 注意事项

- 不要提交 API keys
- 不要提交 `.venv/`、`.cache/`、`models/`、`uploads/`、`work/`、`input/`、`archive/`、`failed/`、`output/`
- 模型文件较大，存放在 `models/` 并排除在 Git 之外
- 翻译结果缓存在 `work/translation-cache/`，相同内容不会重复调用 API
