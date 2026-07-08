# 智译字幕工坊 / CineSub Studio v0.4 Desktop Preview

## Tester Onboarding Note（测试者快速上手说明）

### 1. 这是什么

- **产品**：智译字幕工坊 / CineSub Studio
- **版本**：v0.4 Desktop Preview（本地桌面预览版）
- **性质**：可内测预览基线，不是正式安装器版本
- **时间范围**：2025-07

这是一个**本地桌面预览版**：通过 Electron 窗口启动本地 Web 工作台，支持视频字幕识别、翻译、任务管理和运行环境诊断。所有处理都在本机完成，不需要上传视频到云端。

---

### 2. 当前能做什么

| 功能 | 说明 |
|------|------|
| Electron 桌面启动 | 双击窗口启动，自动打开 Web 工作台 |
| 单文件字幕识别 | 选择单个视频，提取音频并转写为 SRT |
| 批量目录处理 | 选择文件夹，批量识别并翻译 |
| 翻译接口设置 | 配置 DeepSeek 等兼容 OpenAI 的 Provider |
| 语言风格选择 | 选择 `fr-film` 等内置 Language Profile |
| 运行环境诊断 | 检查 FFmpeg、模型、Python 版本、目录状态 |
| 最近任务 | 查看历史处理任务、状态和输出 |
| 输出下载 | 下载生成的 SRT 字幕和质量报告 |

---

### 3. 开始之前需要准备

#### 必须项

| 要求 | 说明 |
|------|------|
| Windows 10/11 | 当前主要测试平台 |
| Python 3.10–3.12 | 已安装并配置 `.venv` 虚拟环境 |
| Node.js + npm | 用于启动 Electron 桌面壳 |
| Electron 依赖已安装 | `desktop/` 目录下已运行 `npm install` |
| FFmpeg 可用 | 系统 PATH 中，或已配置 `CINESUB_FFMPEG` / `FFMPEG_PATH` |
| faster-whisper 模型 | 已下载到 `models/` 或缓存目录 |
| DeepSeek API Key | 或其他兼容 OpenAI 接口的翻译 Provider |

#### 检查清单

启动前，在终端执行：

```powershell
# 检查 Python 和 .venv
.\.venv\Scripts\python.exe --version

# 检查 Electron 依赖
cd desktop
npm ls electron

# 检查 FFmpeg
ffmpeg -version

# 检查模型（如果已下载）
ls models/ -Filter "*.bin" -ErrorAction SilentlyContinue
ls .cache/huggingface/ -ErrorAction SilentlyContinue
```

---

### 4. 启动方式

#### 推荐方式：桌面预览（Electron）

```powershell
cd desktop
npm start
```

Electron 窗口将自动打开，后端服务 `http://127.0.0.1:7860` 自动启动。关闭 Electron 窗口时后端自动清理。

#### 备选方式：Web 后端快速检查（无 Electron）

```powershell
.\start_web.ps1 -Smoke -NoBrowser -NonInteractive
```

这是只检查启动就绪性的无浏览器模式，不处理媒体、不加载模型、不下载。

#### 调试方式：单独启动 Web 后端

```powershell
.\start_web.ps1
```

然后手动打开浏览器访问 `http://127.0.0.1:7860`。

---

### 5. 首次使用流程

#### 步骤 1：确认运行环境

打开 Web 工作台 → 点击 **运行环境** 标签页。

检查项目：
- ✅ FFmpeg 状态
- ✅ Python 版本
- ✅ 模型目录/缓存状态
- ✅ 输出目录可写性
- ✅ 翻译接口状态（可稍后配置）

如有红色错误项，按提示修复后再继续。

#### 步骤 2：配置翻译接口

打开 **翻译接口** 标签页：
1. 添加 Provider：DeepSeek（或你的兼容接口）
2. 填入 API Base URL
3. 填入 API Key
4. 选择翻译模型（如 `deepseek-chat`）
5. 设为 Active Provider

> ⚠️ API Key 只在本地保存，不会上传到任何服务器。不要截图分享含 API Key 的页面。

#### 步骤 3：选择语言风格

打开 **语言风格** 标签页：
- 转写源语言选择：如法语选 `fr`，英语选 `en`
- 翻译目标语言选择：如中文选 `zh`
- 内置 profile：`fr-film`（法语影视）等

#### 步骤 4：单文件处理（体验流程）

1. 准备一个短视频（建议 30 秒–2 分钟，格式 mp4/mkv/mov 均可）
2. 在主页面点击 **选择文件** → 选择视频
3. 确认语言设置
4. 点击 **开始处理**
5. 等待任务完成（首次会下载模型，可能需要几分钟）
6. 在 **最近任务** 查看结果

#### 步骤 5：批量处理（真实场景）

1. 把视频放入 `input/` 目录
2. 在 Web 页面点击 **选择文件夹** → 选择 `input/` 目录
3. 点击 **开始处理**
4. 等待后台流水线完成
5. 在 **最近任务** 查看结果和下载

#### 步骤 6：下载输出

处理完成后，在任务详情中点击下载：
- `source.srt` — 原文转写字幕
- `zh.srt` — 中文字幕
- `bilingual.srt` — 双语字幕（原文 + 中文）
- `quality_report.json` — 质量报告
- `review_needed.srt` — 需要人工复核的片段

---

### 6. 已知限制

这是 **preview baseline**，不是正式安装器版本，请知悉：

| 限制 | 说明 |
|------|------|
| ❌ 没有安装器 | 需要手动准备环境（Python、Node、npm） |
| ❌ 不内置 Python | 需要本地已有 `.venv` 或自行安装 |
| ❌ 不内置 FFmpeg | 需要手动安装或配置环境变量 |
| ❌ 不内置模型 | 首次使用需下载 faster-whisper 模型 |
| ❌ 不保证离线模型下载 | 网络不稳定时可能需手动下载模型 |
| ❌ 翻译依赖网络 | DeepSeek API 调用可能因网络失败 |
| ❌ 无代码签名 | Windows 可能提示未知发布者 |
| ❌ 无自动更新 | 新版本需手动替换 |
| ❌ 暂无 TTS/配音 | 仅字幕生成，无配音功能 |
| ⚠️ 只做 SRT 输出 | ASS 格式是预留参数，不生成 `.ass` 文件 |

---

### 7. 给测试者的反馈模板

发现问题时，请记录以下信息：

```text
系统：Windows 10 / 11
Python 版本：___
Node 版本：___
Electron 版本：___
FFmpeg 来源：系统PATH / 手动安装 / 内置 / 未安装

启动测试：
- [ ] 执行 `cd desktop && npm start` 成功
- [ ] Electron 窗口正常打开
- [ ] 首页加载成功
- [ ] 运行环境页无红色错误（或请列出）
- [ ] 翻译接口页可正常配置
- [ ] 语言风格页可正常切换
- [ ] 最近任务页可正常加载
- [ ] 关闭 Electron 后后端进程已终止

处理测试：
- 视频语言：___
- 视频长度：___
- 模型：___（如 `small` / `base` / `large-v2`）
- 是否成功生成 source.srt？
- 是否成功生成 zh.srt？
- 是否成功生成 bilingual.srt？
- 翻译是否完整（有无漏行）？
- 质量检查是否通过？
- 输出文件是否可下载？

遇到的错误：
- 错误现象：___
- 复现步骤：___
- 日志片段（如 `logs/web_server.log`、`logs/pipeline.log`）：___
```

---

### 8. 快速故障排除

| 问题 | 可能原因 | 解决方向 |
|------|----------|----------|
| `npm start` 失败 | 未安装 Electron 依赖 | `cd desktop && npm install` |
| Electron 打开但白屏 | 后端未启动 | 检查 `logs/web_server.log` |
| FFmpeg 红色 | 未安装或路径不对 | 安装 FFmpeg 或设置 `CINESUB_FFMPEG` |
| 模型下载卡住 | 网络或 Hugging Face 访问 | 手动下载后放入 `models/` 或 `.cache/huggingface/` |
| 翻译失败 | API Key 无效或网络不通 | 检查 Provider 配置，确认 API 可用 |
| 关闭 Electron 后后端还在 | 异常退出 | 手动结束 `python.exe` 进程，检查 `logs/` |
| 中文路径问题 | 编码问题 | 确认路径无特殊字符，或放英文目录测试 |

---

### 9. 版本基线信息

- **Release cut commit**：`f8c3251` `v0.4: cut desktop preview baseline`
- **Baseline commit**：`684ed09` `v0.4.1: harden translation structured output handling`
- **Tag**：`v0.4-desktop-preview`（本地 tag，未 push）
- **Date**：2025-07-08

---

*本 onboarding note 随 v0.4 Desktop Preview 基线一起维护。如有问题，优先检查 `logs/` 目录和 `README.md` 的常见问题章节。*
