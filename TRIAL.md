# CineSub Studio 外部试用说明

## 1. 这是什么

CineSub Studio 是一个 Windows 本地字幕工具。这个试用包可以把视频或音频转写为 SRT 字幕，也可以在配置翻译 Provider 后生成中文字幕或双语字幕。

本次试用对象是现有 RC 包：

```text
dist/cinesub-portable-m6.7-rc1.zip
```

这是 zip 便携包，不是安装程序，也不是 PyInstaller EXE。解压后从文件夹里启动，不需要修改系统 PATH。

## 2. 系统要求

- Windows 10 或 Windows 11。
- 当前用户对解压目录有写入权限。
- 建议至少预留 2 GB 可用磁盘空间；如果下载 Whisper 模型，需要更多空间。
- 首次下载模型、配置在线翻译或调用 Provider 时需要网络。
- 转写需要 Whisper 模型；翻译需要可用的 Provider API Key、API Base 和模型名。

## 3. 下载/解压

把 zip 解压到一个普通用户可写的目录，例如桌面、下载目录或单独的工作目录。

可以使用中文路径，但如果遇到启动或处理失败，请在反馈里说明解压路径是否包含中文、空格或特殊符号。

解压后应看到：

```text
cinesub-portable/
```

## 4. 启动 start_app.bat

进入 `cinesub-portable/` 目录，双击：

```text
start_app.bat
```

如果双击后窗口闪退，可以在 PowerShell 里进入该目录再运行 `.\start_app.bat`，并保留错误文本或截图用于反馈。

## 5. 打开 http://127.0.0.1:7860

启动成功后，浏览器会打开：

```text
http://127.0.0.1:7860
```

如果没有自动打开，可以手动复制这个地址到浏览器。Web 服务只绑定本机 `127.0.0.1`。

## 6. 配置 Provider

如果只做转写和本地质量检查，可以先不配置 Provider。

如果需要翻译，请在 Web 页面里配置 Provider：

- API Base
- API Key
- 翻译模型
- active provider

API Key 只应写在 Provider 配置里，不要写进 Language Profile，也不要发给测试收集者。

## 7. 放入 input/

把要处理的视频或音频放入解压目录下的：

```text
input/
```

常见格式包括 `mp4`、`mkv`、`mov`、`avi`、`mp3`、`m4a`、`wav`。

大文件建议直接复制到 `input/`，不要通过浏览器上传整部电影。

## 8. 扫描、开始处理、查看状态

在 Web 页面里按顺序操作：

1. 点击“扫描 input”。
2. 确认任务列表。
3. 点击“开始处理 input”。
4. 在“任务状态”“操作日志”“异常复核”里查看进度和问题。

同一时间只允许一个后台流水线任务运行。如果提示已有任务运行，请等待当前任务结束后再试。

## 9. 下载字幕和报告

默认输出位于：

```text
output/source/      原文 SRT
output/zh/          中文字幕 SRT
output/bilingual/   双语字幕 SRT
output/reports/     质量报告和 review_needed.srt
```

Web 页面会显示可下载产物。只有项目 `output/` 目录下存在且非空的文件可以通过 Web 下载。

## 10. 已知限制

- 当前 RC 不包含 Whisper 模型、CUDA 离线包或 wheelhouse。
- Provider 未配置时，Web 可以启动，转写也可以运行，但翻译会失败。
- 当前只稳定输出 SRT；ASS 是预留能力，请求 ASS 时不会生成 `.ass` 文件。
- 混合语言视频会按一个主要源语言处理，不做分段语言识别。
- 很长视频、噪声很重的视频、多人重叠说话或口音很强的素材，可能需要人工复核。
- 首次模型下载可能较慢，也可能受网络环境影响。

## 11. 反馈模板

反馈问题时，请尽量提供：

```text
Windows 版本：
解压路径：
解压路径是否包含中文、空格或特殊符号：
启动截图或错误文本：
diagnostics 截图或文本：
输入文件类型和大致时长：
任务状态：
output/reports 里的报告摘要：
复现步骤：
期望结果：
实际结果：
```

如果问题和某个字幕结果有关，可以摘录少量不敏感字幕片段说明现象。不要发送完整私人视频、完整字幕或包含隐私内容的大段日志。

## 12. 隐私与 API key 安全

请不要分享以下内容：

- API Key、token、secret。
- 完整 Provider 配置。
- 私人视频、私人音频或完整字幕内容。
- 可能包含 API Key 或隐私路径的完整日志。
- 浏览器截图中可见的完整密钥。

如果需要反馈 Provider 问题，请先打码密钥，只保留 API Base、模型名、错误类型和必要的错误文本。
