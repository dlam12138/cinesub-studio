# 轮次记录：VideOCR 中文路径 remediation

- 时间：2026-07-22
- 用户目标：冻结 v0.7.1 验收环境，执行六视频预筛并生成匿名候选。

## 已知事实与证据

- 首次真实 VideOCR GPU 预筛写入了系统 `LOCALAPPDATA` 和系统临时目录，随后在 PaddleOCR predictor 初始化阶段失败。
- 失败日志显示打包模型配置文件均非空，错误为底层空 JSON 解析，而不是 OCR 语言或 ROI 参数拒绝。
- 重定向用户和缓存目录后，错误日志已正确留在私有验收目录，但中文临时路径下 GPU predictor 仍失败。
- 通过 ASCII junction 启动 VideOCR，并将第三方 runtime 指向独立 ASCII 私有目录后，同一窗口 GPU OCR 成功并生成非空 SRT。

## 决策摘要

- 首次预筛视为 Attempt 1 环境失败，不用于最终候选或结论。
- 在正式矩阵前执行 remediation commit，完整回归、重新预检、重新冻结，然后从六视频预筛起点重跑。
- 不更改 OCR 阈值、ASR Profile 或 retry 规则；只修正第三方进程路径和缓存隔离。

## 执行操作

- runner 新增第三方 `LOCALAPPDATA`、`APPDATA`、`TEMP`、`TMP` 和 Paddle 缓存重定向。
- runner 新增可选 `--runtime-root`，用于提供 ASCII 私有临时根；产品代码不使用该参数。
- VideOCR 可执行路径不再解析 junction 目标，确保第三方工具看到纯 ASCII 工具根。
- 新增运行环境重定向和 junction 路径保留测试。

## 验证结果

- VideOCR 定向测试 7 项通过。
- 同一真实 60 秒窗口在 ASCII 隔离环境中以 GPU 成功运行并生成非空 SRT。
- 完整 `pytest -q`、导入检查、两个 self-test、Web smoke、两个 HTTP 200、Electron JavaScript 语法检查和 `git diff --check` 全部通过。

## 未解决问题与下一步

- 创建并推送 remediation commit，以新 SHA 重做 VideOCR/模型预检和环境冻结。
- 清空 Attempt 2 的私有预筛结果目录，保留 Attempt 1 失败证据，然后重跑六视频预筛。
