# 阶段五：ASR 算法证据驱动优化进展

记录日期：2026-07-12
状态：`in_progress`

## 已完成的工程能力

- 建立固定候选注册表和显式参数白名单，未知 candidate、字段或不允许的模式会被拒绝。
- Language Profile、单文件 CLI、Pipeline CLI 和 Web Pipeline 已支持 `asr_experiment_mode` 与 `asr_candidate_id`，优先级为 CLI > Profile > 默认 `off`。
- 保持 `transcribe_to_srt()`、SRT 命名、默认 ASR routing、ASS reserved 和 retry-failed 语义兼容。
- 新增结构化转写产物与脱敏候选报告；报告只记录 cue 数、语言概率、时间边界、质量信号、哈希和选择结果，不记录字幕正文或绝对路径。
- `dry_run` 始终保留 baseline；候选异常、空输出、时间轴异常、CUDA 或合并失败均回退完整 baseline，不留下部分候选字幕。
- `apply` 只接受注册表明确批准的候选。当前没有候选获批，因此没有新增 `optimized-asr-preview` Profile，也没有改变任何内置默认 Profile。

## 固定 FLEURS 初筛

基线为阶段三冻结的 `large-v3-cuda-float16`。四个可直接解码候选分别在目标样本执行 3 次：

| 候选 | 目标结果 | 决定 |
|---|---|---|
| `vad-balanced-v1` | CER、missed-cue 和 duplicate-cue 无改善 | 不进入完整 90 次评测 |
| `vad-sensitive-v1` | 复杂英语 CER 退化约 1.7%，RTF 约 1.13 倍 | 不进入完整评测 |
| `decode-repeat-guard-v1` | 重叠样本无改善；复杂英语 CER 退化约 0.7%，RTF 约 1.12 倍 | 不进入完整评测 |
| `previous-text-off-v1` | 目标样本无改善 | 仅保留对照，不批准 apply |

初筛报告保存在本地忽略目录 `output/reports/asr_benchmark/stage5-screen/`。没有候选达到“全体 CER 至少改善 5%、目标子集至少改善 10%”的晋级门槛，因此按计划停止扩大运行，未用无效候选消耗完整 90 次矩阵。

`local-retry-v1` 和 `mixed-route-v1` 仍只允许 `dry_run`。前者已具备结构规则、窗口扩展、合并校验与完整回退；后者继续复用现有 routing evidence。二者尚未获得自然语料晋级证据。

## CAFE-small challenge suite

- 来源：Zenodo record `16964503`，revision `v2.0.0`。
- 许可：CC BY 4.0。
- 归档：`cafe-small.zip`，`320908945` bytes，MD5 `c03ab09435ab8cd95e62c38c9c72dbd1`。
- 下载、缓存、解压、音频和逐字稿均位于项目内忽略目录，不重新分发。
- 从 128 个满足时长的片段中固定选择 12 个 30–90 秒样本，覆盖法语较多、英语较多、自然阿拉伯语/法语/英语切换、笑声、环境噪声和键盘声。
- 本地 provenance manifest fingerprint：`438725784c75d152f74509e3db0d16a395d2d917dfe8251b062b75398e98fd48`。
- Benchmark 内容 fingerprint：`DE54B711A31A40936C9515EA5A451CDD218929C9A95BF66F7001D426F03DDAAA`。

CAFE baseline 已完成 `12 samples × large-v3 CUDA × 3 repeats`，36/36 成功。聚合 CER mean 为 `0.598219`，missed-cue rate 为 `0`，duplicate-cue rate mean 为 `0.009114`，RTF mean 为 `0.300721`；模型强制从项目内缓存加载。

`local-retry-v1` 已接入 benchmark 的真实“baseline → 可疑窗口重试 → 合并校验”路径。三个目标样本筛选结果分别为：自然切换噪声样本 CER 改善约 9.4%；噪声英语样本无改善；笑声样本 CER 退化 7.63% 且 RTF 增加 29.8%。因此不进入完整矩阵，不批准 apply。

`local-retry-selective-v2` 增加共享模型 session、逐窗口结构质量比较和配对 benchmark。同一 worker 内先生成 baseline，再只重试可疑窗口；候选仅在覆盖、重复、no-speech、可疑项均不退化且 logprob/compression 等至少一项明确改善时合并。CAFE 三个目标样本 9 次配对运行中共触发并接受 4 个窗口，但聚合 CER 从 `0.479109` 退化到 `0.482412`；笑声样本每次退化约 4.6%–5.0%。FLEURS 重叠样本 CER 仅改善约 1.38%，触发重试的增量耗时约为 baseline 的 41%–43%；远距样本没有触发窗口。候选未达到 10% 目标改善和 25% 增量耗时门槛，因此保持 dry-run，不执行完整矩阵。

`mixed-route-v1` 已增加 routing-only evidence 入口。12 样本运行超过外层 15 分钟时限且未形成原子报告，不计入证据。随后三个自然切换目标样本完成 dry-run：10/10 分析窗口全部被分类为 `needs_review`，没有窗口进入 `keep_auto`、`prefer_forced_fr` 或 `prefer_forced_en`；无 fallback，字幕输出均未受影响。该结果不能证明自然代码切换已经解决，因此 mixed routing 保持 dry-run。

重要限制：CAFE-small 的该发布包提供 clip-level ZAEBUC 转写和事件标记，但没有逐时段说话人或重叠边界。它主要评估阿尔及利亚阿拉伯语中的自然法语/英语切换，并不是纯法英自然切换集。重叠覆盖不得从文件名推断，仍需人工听审或具有说话人时间轴的补充金标。

## 自动化验收

- ASR candidate、接口和 benchmark 专项测试：`28 passed`。
- 全量 pytest：`451 passed`。
- 基础导入、字幕翻译自测、质量检查自测：通过。
- Pipeline `--scan` 与 `--status`：通过；`--review` 正常读取 8 份历史报告，并因其中已有 8 个质量错误按既有语义返回 1。
- 源码 Web smoke：通过；首页和 runtime diagnostics API 均返回 200。
- `git diff --check`：通过。

## 未满足的完成门槛

- 尚无候选在固定语料连续两轮完整运行中达到 promotion gates。
- challenge suite 尚未完成 baseline/candidate 的完整指标比较。
- 自然代码切换的 MER、切换点后首词错误率、语言段召回率尚未形成可信报告。
- 法语剧情、远距采访、重叠对话、复杂英语和自然混合语言各一段的真实影片人工复核尚未完成。

因此阶段五保持 `in_progress`。当前默认生产行为继续为 `off`，不得创建 apply Profile 或把路线图改为 `completed`。

## 2026-07-15 收口能力补齐与当前决定

- Benchmark 已增加 transcript-local `code_switch_metrics`：MER、切换点后首词错误率和语言段召回率。缺少语言边界或 hypothesis 分段语言证据时返回 `null` 与明确 warning，不从文件名或全局语言猜测伪造指标。
- 本地 manifest 可通过可选 `language_annotations` 关联人工语言段标注；标注内容、字幕正文和听审媒体继续只保存在 gitignored 目录。
- Benchmark 已增加稳定 `--run-id`、`--resume`、原子 checkpoint 和 `--routing-timeout-seconds`。恢复时严格校验 corpus fingerprint、候选、配置、repeat 数和 `local_files_only=true`；损坏或不匹配的 checkpoint 会明确失败。
- 新增匿名五类 A/B 听审包工具和独立 Go/No-Go 汇总工具。决策工具不会自动修改候选注册表、Language Profile 或生产默认值。
- 使用现有 CAFE `local-retry-selective-v2` 9 次配对报告重新生成正式决定：总体及目标子集 CER 相对变化均为约 `-0.69%`，时间轴 P95 退化，未达到 5%/10% 门槛，决定为 `no_go`。
- 使用现有 mixed-route 三样本报告汇总：10/10 窗口为 `needs_review`，无 forced-language 选择，且 MER、切换点首词错误率和语言段召回率仍缺人工金标，因此 `mixed-route-v1` 不具备 promotion 条件。
- 2026-07-15 尝试重跑同一 local-retry 初筛时，首个样本超过本轮单样本运行预算且未形成完整结果；该次运行已停止且不计入证据，不替代既有 9 次配对报告。
- 已生成 12 个 CAFE 本地语言段标注模板和五类听审 manifest。当前自然混合语言真实媒体尚未核验，五类人工听审均未完成。
- 实施后验收：全量 `464 passed`，并将 `PytestUnhandledThreadExceptionWarning` 视为 error；基础导入、翻译/质检自测、Python 编译、`git diff --check`、Pipeline scan/status/review 均完成。真实 Web 首页返回 200，runtime diagnostics 返回 `status=ok`、`ffmpeg_source=bundled`，稳定字段完整。
- 私有执行基线已保存到 `output/reports/stage5-closeout/baseline_snapshot.local.json`，记录当前 Git 状态、两套语料 fingerprint、Python 3.12、FFmpeg、CUDA、驱动、本地模型和测试摘要；该文件不提交。

当前决定仍为 `no_go`：不执行完整矩阵、不批准任何候选 `apply`、不新增 optimized Profile，生产默认继续为 `off`。阶段五继续保持 `in_progress`；下一有效动作是完成人工语言段标注和真实媒体听审，或提出新的候选假设后重新初筛。

## 2026-07-16 真实硬字幕短视频来源登记

用户补充了四个原始 Bilibili 页面。通过页面元数据、CID、时长和本地媒体时长比对，已确认它们分别对应本地 `布林肯`、`户外采访`、`音乐剧` 和 `西语` 四个样本。四个页面的播放器字幕轨数量均为 0；其字幕属于画面硬字幕，不能作为 Bilibili 软字幕直接下载。

本地以 1 秒均匀取帧、底部 320 像素裁剪和 Windows OCR 生成弱标注候选，结果只保存在 gitignored 的 `work/bilibili-subtitles/`。公开记录只保留 BVID、页面元数据、数量和哈希，不包含媒体、字幕正文或绝对路径。

| 样本 ID | 语言/场景 | OCR cue | 源语非空 | 译文非空 | 使用决定 |
|---|---|---:|---:|---:|---|
| `BV1xa4y1w7XY` | 法语广播采访 | 117 | 114 | 68 | 弱标注/错误挖掘 |
| `BV1aM4m197R8` | 法语户外采访 | 541 | 383 | 535 | 弱标注/噪声与漏句挖掘 |
| `BV1vs411z7pw` | 法语音乐剧演唱 | 65 | 63 | 62 | 弱标注/演唱域压力测试 |
| `BV1Ef4y1x7hR` | 西班牙语采访 | 213 | 213 | 203 | 弱标注/跨语言回归 |

由于用户无法进行人工逐句校对，这批 OCR 不升级为人工金标，不写入冻结 benchmark 的 `reference_srt`，也不参与 CER/MER 晋级门槛计算。它们可用于自动发现空输出、明显漏句、重复、时间覆盖缺口和候选回退问题；任何候选 Go/No-Go 仍须以现有固定金标语料为准。该登记不改变当前 `no_go`、`dry_run` 和生产默认 `off`。

## 2026-07-16 OCR 弱标注对照闭环

- 新增独立 `ocr_evidence_compare.py`，以纯时间轴、单调、多对多组件对齐 OCR、ASR 和译文；文本相似度不参与对齐。
- OCR 提取器新增 `ocr-evidence.local.json`，只记录采样帧、非空状态和跨帧稳定度。Windows OCR 不提供模型置信度，工具没有伪造 confidence；单帧观察固定为低稳定度 `0.5`。
- 默认 LLM 裁判为 `off` 且预算为 `0`。只有同时显式提供 `--llm-judge uncertain`、Provider 和正数预算时才允许请求；裁判使用随机 A/B 标签、相邻窗口和本地缓存。
- 报告结论限制为 `insufficient_evidence`、`rejected_by_weak_screen` 和 `eligible_for_gold_benchmark`；最后一种也固定 `apply_allowed=false`。
- 已复用四个样本现有 `raw-ocr.local.json` 生成 sidecar，没有重新 OCR、加载 ASR 模型或调用 LLM。首次 baseline 报告位于忽略目录 `output/reports/ocr_evidence/bilibili-baseline-v1/`，公开摘要 SHA-256 为 `44EA6BF3550A2BB762284CB6FC7B6AC37E42A9E57D87DDDB3F9F74C05FF75877`。

| 样本 | 高稳定 OCR 覆盖 | 源文 disagreement | OCR 有字/ASR 无覆盖 | ASR 重复率 | 译文阻断项 |
|---|---:|---:|---:|---:|---:|
| `BV1xa4y1w7XY` | 0.200555 | 2.875294 | 0 | 0 | 0 |
| `BV1aM4m197R8` | 0.277753 | 10.562998 | 0.037234 | 0.007782 | 29 |
| `BV1vs411z7pw` | 0.586538 | 0.784247 | 0 | 0 | 6 |
| `BV1Ef4y1x7hR` | 0.579179 | 0.679432 | 0 | 0 | 0 |

编辑差异允许因 ASR 插入文本而大于 `1.0`，不能解释为准确率。四个样本的高稳定 OCR 覆盖均低于 60% 晋级门槛，且本轮只有 baseline、没有候选，因此只建立错误分布，不形成候选晋级结论。阶段五和翻译可靠性的既有 `no_go`、生产 `off` 均不改变。

实施后专项、ASR benchmark 和翻译可靠性回归通过；全量 `512` 项 pytest 通过。基础导入（含新工具）、翻译/质检自测、Python 编译和 `git diff --check` 通过。Pipeline scan/status 返回 0，review 按 8 份既有质量报告发现问题并保持返回 1；真实 Web 首页返回 200，runtime diagnostics 返回 `status=ok`、`ffmpeg_source=bundled`，测试进程树随后已停止。该功能未加载 ASR 模型、未下载模型，因此没有新增 `local_files_only` 运行路径。
