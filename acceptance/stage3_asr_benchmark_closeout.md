# 阶段三 ASR 评测基线验收摘要

验收日期：2026-07-11
状态：`completed`

## 语料与授权

- 语料来源：Google FLEURS，固定 revision `70bb2e84b976b7e960aa89f1c648e09c59f894dd`。
- 使用分片：`fr_fr`、`en_us`、`cmn_hans_cn` 的 test split；许可为 CC BY 4.0。
- 本地构造 10 个 30–90 秒样本：法语 6、英语 2、法英混合 1、中英混合 1。
- 样本覆盖干净语音、确定性粉红噪声、重叠语音、快速语音、远距低音量和语言交替。
- 原始分片、媒体、金标 SRT、provenance 和 manifest 均保存在 gitignored 项目目录，不提交字幕正文或媒体。

## 冻结运行

- Baseline：10 样本 × 3 配置 × 3 次，共 90 次，全部成功。
- Candidate comparison：同一语料再次运行 90 次，全部成功。
- 两轮共 180 次 ASR；两轮 corpus fingerprint 均以 `98509F453904D67E` 开头，候选报告 `compatible_corpus=true`。
- 所有运行均报告 `local_files_only=true`，未下载模型、未调用翻译 API。
- Baseline 另执行 10 个 segment routing `dry_run`，全部为 `dry_run_complete`；未启用 routing apply，未接受 routed SRT。

Baseline 汇总：

| 配置 | 成功/失败 | CER mean | RTF mean |
| --- | ---: | ---: | ---: |
| `small-cpu-int8` | 30 / 0 | 0.476817 | 0.225890 |
| `small-cuda-float16` | 30 / 0 | 0.489766 | 0.084578 |
| `large-v3-cuda-float16` | 30 / 0 | 0.346071 | 0.177225 |

Candidate comparison 汇总：

| 配置 | 成功/失败 | CER mean | RTF mean |
| --- | ---: | ---: | ---: |
| `small-cpu-int8` | 30 / 0 | 0.485399 | 0.254826 |
| `small-cuda-float16` | 30 / 0 | 0.474426 | 0.085980 |
| `large-v3-cuda-float16` | 30 / 0 | 0.346071 | 0.176204 |

## 脱敏与回归

- JSON/Markdown 报告扫描未发现 API Key、Authorization 值、字幕正文或绝对媒体/金标路径。
- 报告只记录样本 ID、语言、声学标签、哈希、指标、环境摘要和 routing 摘要。
- ASR benchmark 专项测试通过；完成时全量测试、基础导入、Pipeline 只读检查、Web smoke 与 `git diff --check` 均需再次通过。

## 限制

- FLEURS 主要是朗读语音；噪声、重叠、远距和快速条件是确定性派生压力样本，不代表真实长片分布。
- 本阶段只冻结评测基线，不依据结果自动切换模型、参数或 routing 模式。
- 干净 Windows VM 安装验收仍按阶段二例外延期，不能由本次 ASR 验收替代。
