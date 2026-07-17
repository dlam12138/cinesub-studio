# Third-Party Notices — 智译字幕工坊 / CineSub Studio v0.6

本安装包包含以下第三方组件的运行时库。相应许可和归属声明如下。

## FFmpeg

- 来源: https://ffmpeg.org/
- 许可: LGPL-2.1+ / GPL-2.0+（取决于编译配置）
- 说明: 本安装包包含 FFmpeg 可执行文件（ffmpeg.exe、ffprobe.exe），
  用于音频抽取和媒体处理。FFmpeg 为独立项目，其代码未与本项目链接。

## NVIDIA CUDA / cuBLAS / cuDNN 运行时

- 来源: NVIDIA Corporation
- 许可: NVIDIA Software License Agreement
- 说明: 本安装包包含 CUDA 运行时动态链接库（cublas64_12.dll、
  cudnn*_9.dll 等），用于 faster-whisper / CTranslate2 的 GPU 加速。
  这些库为 NVIDIA 专有软件，按 NVIDIA 许可条款分发。
- **重要**: 本安装包包含 CUDA 运行库，但不包含 NVIDIA 显卡驱动。
  GPU 加速需要用户电脑已安装兼容的 NVIDIA 驱动。

## Python 运行时与依赖

- Python: https://www.python.org/ (PSF License)
- faster-whisper: https://github.com/SYSTRAN/faster-whisper (MIT)
- ctranslate2: https://github.com/OpenNMT/CTranslate2 (MIT)
- 其他依赖见 requirements.txt 及各自仓库许可。

## Electron

- 来源: https://www.electronjs.org/
- 许可: MIT

## 界面字体

- Barlow Condensed: Copyright The Barlow Project Authors，SIL Open Font License 1.1。
- Noto Sans SC: Copyright The Noto Project Authors，SIL Open Font License 1.1。
- 字体文件和完整 OFL 文本位于 `web/assets/fonts/`，随离线界面一起交付；运行时不访问字体 CDN。

---

本软件为预览版本，无代码签名，无自动更新，不作为最终公开发行版。
