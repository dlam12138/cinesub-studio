# 离线 ASR 研究资产

此目录及仓库中相应的 `src/tools/` 研究脚本、历史验收记录和冻结测试数据，不属于当前产品能力。

- 产品主流程只使用 faster-whisper，并提供 `auto`、`fixed`、`multilingual` 三种模式。
- FunASR、WhisperX、ASR candidate、`mixed-route-v1`、segment routing 和 OCR 裁决不会由 Web、Pipeline、安装器或运行环境下载计划调用。
- `research/requirements/` 中的依赖清单只供复现实验使用，不应并入基础安装或产品 wheelhouse。
- 研究工具的输出只可作为离线证据，不会触发产品字幕改写、模型切换、局部重跑或结果替换。

OCR 弱证据流程的边界和结果解释见 [`../docs/ocr_weak_evidence_evaluation.md`](../docs/ocr_weak_evidence_evaluation.md)。
