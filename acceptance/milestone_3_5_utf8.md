# Milestone 3.5 Acceptance: UTF-8 Runtime Handling

日期：2026-07-01

## 验收结果

- `pytest tests/test_encoding_utils.py tests/test_srt_utils.py` passed: 8 passed

## 已完成能力

- Python 文本、JSON、子进程输出统一通过 UTF-8 helper 处理。
- PowerShell 启动器设置 UTF-8 控制台和 Python IO 环境变量。
- SRT 读取接受 UTF-8 BOM，并保留中文路径和字幕内容。
- FFmpeg 下载校验和转写音频抽取使用统一子进程文本解码。

## 注意事项

- 本次只提交源码、测试和验收说明，不提交运行产物或离线包内容。
