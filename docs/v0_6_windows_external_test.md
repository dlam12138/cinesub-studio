# v0.6 Windows 外测说明

v0.6.1 是无需命令行操作的 Windows x64 外测版本，只提供一个完整离线安装包。

安装包携带 portable Python、FFmpeg 和当前 CTranslate2 对应的 CUDA 运行库。设备默认使用 `auto`：只有 CUDA 运行库、Python 依赖和兼容的 NVIDIA 驱动全部就绪时才使用 GPU，否则自动回退 CPU。用户显式选择 CUDA 而环境不完整时会返回可诊断错误，不会静默改为 CPU。

安装包不包含 Whisper 模型、NVIDIA 驱动、API Key、自动更新或代码签名。Windows SmartScreen 可能显示未签名警告。

## 首次启动

1. 运行 `CineSubStudio-0.6.1-windows-x64-setup.exe` 并启动“智译字幕工坊”。
2. 首页“开始前检查”会显示运行环境、翻译接口、语言配置和任务准备状态。
3. 在“运行环境”确认 FFmpeg，并按明确提示将模型导入项目数据目录；应用不会静默下载模型。
4. 只需要原文字幕时可以不配置翻译接口，并在任务参数里关闭翻译。
5. 需要翻译时再进入“翻译接口”保存 OpenAI-compatible 配置；界面只回显脱敏后的 API Key。

## 用户数据

- 模型、输出、工作文件、日志和缓存：`%LOCALAPPDATA%\CineSubStudio\`
- Provider 与 Language Profile 覆盖：`%APPDATA%\CineSubStudio\config\`

卸载应用不会擅自删除上述用户数据。删除前请自行备份字幕成品和配置。

## 外测反馈

请记录 Windows 版本、CPU/GPU、驱动版本、应用版本、实际选择的运行设备、复现步骤和界面显示的脱敏错误摘要。不要提交 API Key、token、完整 Provider 配置或包含隐私的原始影片。
