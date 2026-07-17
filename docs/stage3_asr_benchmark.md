# 阶段三：ASR 评测基线实施说明

## 目标与边界

阶段三建立可量化、可复现、可比较的 ASR 基线，不改变 `transcribe_to_srt()`、Web、Pipeline 或默认 segment routing 行为。所有真实运行强制 `local_files_only=True`，不下载模型，不调用翻译 API，不把媒体、金标文本或逐字稿提交到仓库。

生成物写入 `output/reports/asr_benchmark/`，临时 WAV、SRT、worker job 和 routing dry-run 产物写入 `.tmp/asr-benchmark/`。

## 金标集

本地 manifest 固定 10 个 30–90 秒短片：

- 法语 6 个：干净对白 2、噪声或音乐 1、多人重叠 1、快速对白 1、远距或低音量 1。
- 英语 2 个：干净对白 1、复杂声学 1。
- 混合语言 2 个：法英 1、中英 1。

每个样本配 UTF-8 原语言 SRT。只标可听见的语音，不写说话人标签或环境音；可辨识口头语保留；无法可靠辨识的 token 写为 `[UNK]`，文字指标计算时忽略。媒体、SRT 和 `manifest.local.json` 必须保持 gitignored。

## 冻结配置

| ID | Model | Device | Compute type |
| --- | --- | --- | --- |
| `small-cpu-int8` | `small` | `cpu` | `int8` |
| `small-cuda-float16` | `small` | `cuda` | `float16` |
| `large-v3-cuda-float16` | `large-v3` | `cuda` | `float16` |

统一参数为 beam 5、VAD 开启、previous-text 开启、local-files-only。冻结运行每个样本/配置重复 3 次。

## CLI 与报告

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\tools\asr_benchmark.py `
  --manifest tests\asr_benchmark\manifest.local.json `
  --output-dir output\reports\asr_benchmark
```

可选参数：`--sample`、`--config`、`--repeat`、`--dry-run`、`--baseline`、`--include-routing-dry-run`。执行前打印样本数、配置、调用次数、设备、模型和输出目录；缺 FFmpeg、本地模型或显式 CUDA 环境时在任何 ASR 工作开始前失败。

报告包含 schema/version、语料指纹、Git/Python/依赖/硬件环境、逐样本结果、配置汇总、重复运行分布、baseline 差值和独立 routing dry-run 摘要。报告不得包含字幕正文、API Key 或绝对媒体/金标路径。

指标口径：

- 所有语种计算 NFKC、大小写和标点归一后的 CER；法语和英语另算空格分词 WER。
- 金标 cue 前后各放宽 0.5 秒后仍无预测 cue 时间重叠，计为漏句。
- 相邻预测 cue 归一化文本完全相同，后一条计为重复。
- 时间轴按最大时间 IoU 匹配，报告起止偏移的 mean、median、P95。
- 性能报告 elapsed、real-time factor、Windows PeakWorkingSetSize 和按 PID 采样的 GPU 峰值显存；能力不可用时写 `null` 和 warning。
- Segment routing 只运行现有 `dry_run`，其耗时和资源不并入普通 ASR 指标，也不接受 routed SRT。

## 完成门槛

阶段三只有在 10 个金标样本、三档各 3 次冻结结果、可读取的候选比较报告、脱敏验收摘要和全量回归均完成后才能标为 `completed`。阶段三已于 2026-07-11 按上述门槛完成，证据见 `acceptance/stage3_asr_benchmark_closeout.md`；后续仍不得仅根据单次报告自动切换参数或启用 routing apply。
