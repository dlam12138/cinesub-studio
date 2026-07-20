# 翻译可靠性 Preview 验收记录

日期：2026-07-16
结论：自动文本阻断门槛 `pass`；人工 A/B `no_go`；公开预览开关 `no_go`。生产默认保持 `off`。

## 实现与回归

- 实现可观测错误分类、有界重试、可拆分错误二分恢复、子批原子缓存和最终 SRT 原子替换。
- 实现共享阻断规则与每 ID 一次的独立修复；非阻断 warning 不修复。
- 接通 Language Profile、CLI、Web 后端和 Pipeline；Pipeline stage event 记录脱敏摘要；未在 Web/Profile 编辑器暴露开关，但编辑时会保留已有隐藏预览配置。
- 全量 pytest：`494 passed`（包含 19 项新增可靠性与接口测试）。
- 新增可靠性核心、验证工具和测试的 Ruff 检查通过；全仓 Ruff 已执行，仍报告 196 个现有架构/ASR/历史工具与测试问题，本轮未批量改写用户既有变更。
- 基础导入、两项核心自测、Pipeline scan/status/review、Node/YAML、CI 策略扫描、`git diff --check`、Web 首页和 diagnostics 200 已执行。Pipeline review 按现有质检报告返回问题状态，status 仍显示一个现有文件权限 warning，未被本轮隐藏或重置。

## 真实 LLM 两轮矩阵

- 样本 ID：`french-short-blockers-v1`
- 样本 fingerprint：`6523edbfef162a6953cd5bcb9028c036fb5478784cee1f482d064e449a988b66`
- 选取 ID 哈希：`a4ba1fbbe1c9287e951c5a4a85a4231a76e999db4ea75cdf541a9dda2017e93a`
- Provider/模型：`deepseek-main` / `deepseek-v4-flash`
- 固定样本：24 cues，两轮 fingerprint 一致。
- baseline 阻断项：2；两轮 preview 均为 0；目标阻断项改善 100%，无新增阻断项。
- 两轮均命中 baseline 缓存 24/24，并分别真实触发 2 个 `identical_translation` 独立修复；修复成功 2/2，未解决 0。
- 第 1 轮输出 SHA-256：`f9ef01d426261fdee9777d464fc7a747ce27fd96e70baf911319a125ad7c5c64`
- 第 2 轮输出 SHA-256：`b8ef8878d067791885db86a094379ca9956aa01527edd4856bbc3ba1f084718a`
- 两轮均完整可解析，cue 数、编号和时间轴零变化。
- 正式缓存修复矩阵 4 次 HTTP；加上干净对照和重新翻译路径的前置探索 4 次，本轮总计 `8/20`。
- 报告仅保存 ID、哈希、调用计数、阻断项变化和缓存/拆分摘要；不含字幕正文、API Key 或绝对路径。

## 人工 A/B

已使用真实原片 `BV1mJ2rBCEe8`（《【中法双语字幕】法国总统马克龙四川大学演讲15分钟完整版》）完成针对性听审。用户确认 A 第 7 条和 B 第 19 条未翻译；匿名答案表明两者均为 baseline，Preview 确实消除了未翻译阻断。

但人工听审同时确认净退化：

- cue 7 的 `à voir` 是 cue 6 的跨 cue 续句，Preview 将其独立解释为“拭目以待”，割裂了原句逻辑。
- cues 17–19 的原意是“面对技术、气候、人口变革，需要更多合作”；Preview 修复 cue 19 后与 cue 18 形成重复的“需要更多合作”，且未恢复完整语义结构。

因此人工结论为 `no_go`。当前自动验收只能证明“阻断规则消失”，不能证明跨 cue 语义正确。

仅当 preview 偏好数不少于 baseline、整体可读性不下降且无新增阻断项时，才能公开非默认开关。

## 决策

- 自动文本阻断门槛：`pass`，但证据不足以支持发布。
- 人工 A/B：`no_go`，存在跨 cue 语义割裂和重复。
- Web/Profile 公开入口：`no_go`。
- 默认翻译可靠性模式：`off`。
- ASR 阶段五结论不变：`no_go`，`asr_experiment_mode=off`。

## 下一轮假设

- 修复请求需同时提供相邻源文和已有译文，并显式识别跨 cue 续句。
- 对明显被相邻译文提前吸收的短片段，单 cue 修复不足；应评估“固定 ID/时间轴的小窗口联合重译”。
- 新增相邻 cue 重复/包含检测和跨 cue 语义人工门槛；不得仅以未翻译规则消失作为 Go 依据。

## 2026-07-17 小窗口联合重译与离线 E2E

- 已将单 cue 修复替换为固定 ID/时间轴的小窗口联合重译：问题 cue 前后各扩展一条，相邻或重叠问题合并为一个原子窗口。
- 请求包含窗口内源文、现有译文和窗口外只读上下文；返回 ID 必须与窗口 ID 完全一致。
- 窗口只有在阻断项消失、无新增阻断项且相邻重复/包含关系不恶化时才整体接受；缓存写入失败时字幕和内存缓存均保持原值。
- 脱敏摘要新增窗口尝试、接受、拒绝和相邻重复拒绝计数，不包含 cue ID、正文、密钥或路径。
- 新增完全离线 Pipeline E2E，测试替身只注入 ASR/LLM 外部边界，实际覆盖发现、阶段调度、SRT 输出、质检、任务状态、事件日志、失败恢复与 `retry-failed` 选择。
- 离线 E2E 分别验证纯译文和双语产物，并确认翻译失败后复用既有音频和源字幕，不访问网络、不加载模型、不读取 Provider，也不写用户运行目录。
- 定向可靠性、结构化输出、Pipeline stage 与离线 E2E 回归：`41 passed`。
- 全量回归：`541 passed`，并将 `PytestUnhandledThreadExceptionWarning` 视为 error。
- 本轮未调用真实或付费 LLM，未执行新的匿名人工 A/B；公开入口和生产默认结论继续为 `no_go/off`。

## 2026-07-17 小窗口真实 LLM schema v2 评测

结论：自动门槛 `no_go`；人工盲评 `not_started`；公开入口和生产默认继续为 `no_go/off`。

- Provider/模型：`deepseek-main` / `deepseek-v4-flash`。
- 修复策略：`window-v2.1-deterministic`，修复请求固定 `temperature=0`。
- 固定样本：24 cues；选取 ID 哈希 `a4ba1fbbe1c9287e951c5a4a85a4231a76e999db4ea75cdf541a9dda2017e93a`。
- 最终矩阵 fingerprint：`0b0009416bc545cee4a7e4e480a5fbf58de3cabf9a3e142341160cf18c528aba`。
- baseline：2 个阻断项、0 个相邻重复/包含项；共享规划得到 2 个修复窗口。
- 第 1 轮：输出 SHA-256 `9495dd6081c3e880fbc1f71781ce7ea699538af1c7a9a9791941ab2c5a263919`；阻断项 0；窗口尝试/接受/拒绝 `2/2/0`；未解决窗口 0；结构和相邻重复稳定。
- 第 2 轮：输出 SHA-256 `d29300dd2c130c8e3ac759d2fea5263acf434863b0059ac7e572115eb1077a70`；窗口尝试/接受/拒绝 `2/0/2`；2 个窗口均因 `blocking_candidate` 原子拒绝，候选共触发 6 个 `identical_translation`；未解决窗口 2；结构和相邻重复稳定。
- 最终矩阵使用 4 次 HTTP；包含提示强化与有界诊断在内的本次授权评测活动累计 `20/20` 次 HTTP，未再追加调用。
- 自动门槛要求两轮均无阻断项、无拒绝或未解决窗口；第 2 轮失败，因此不生成窗口级 A/B、答案表或本地音频，也不开始人工盲评。
- schema v2 报告仅记录哈希、计数、状态、拒绝类别和预算，不含字幕正文、密钥或绝对路径。
- 全量回归：`547 collected / 547 passed`；变更相关 Ruff、CI 策略、项目 smoke 与 `git diff --check` 通过。首次全量执行曾遇到一次 Windows 本地 socket `WinError 10053`，隔离复跑及随后全量复跑均通过。
- 项目 smoke 验证基础导入、两项核心 self-test、Pipeline 只读命令、首页与 diagnostics HTTP 200；现有 Pipeline review 的 188 项报告问题按原样保留。
- ASR 未参与本评测，代码、候选、评测和默认值均不变，`asr_experiment_mode=off`。

## 2026-07-17 质量优先修复链 schema v3 评测

结论：自动门槛 `pass`；人工盲评 `pending`；发布与生产默认继续为 `no_go/off`。

- 运行 ID：`20260717T071644Z-30adbf`；报告 schema `3`。
- Provider：`deepseek-main`；主模型 `deepseek-v4-flash`；质量候选与判定模型 `deepseek-v4-pro`。
- 策略：`window-v3-quality-chain`；样本 fingerprint `03b0c0acbd06e9b26dbc7525aa19bfc52c8df5c8f4a3f7a2b8e09c8704b206d7`。
- 固定样本 24 cues；选取 ID 哈希 `a4ba1fbbe1c9287e951c5a4a85a4231a76e999db4ea75cdf541a9dda2017e93a`；baseline 有 2 个阻断项、0 个相邻重复/包含项和 2 个修复窗口。
- 第 1 轮：输出 SHA-256 `16f76fd67a134d4ff2c347108d994a3edf39e8a41c1f79e081a1707c657b2bfc`；请求 8 次；窗口尝试/接受/拒绝 `2/2/0`；阻断项与未解决窗口均为 0。一次 Flash 首选候选触发纠正，一次质量候选因空译文被确定性规则淘汰，判定器仍从有效候选中完成选择。
- 第 2 轮：输出 SHA-256 `a7ce8b245f32e9f2b1806c42ae0ef784564ed00c88482fd58e6e3516e5a40a08`；请求 6 次；窗口尝试/接受/拒绝 `2/2/0`；阻断项与未解决窗口均为 0。
- 两轮 cue 数、ID、时间轴不变，相邻重复增量均为 0；质量模型可用，预算未耗尽。
- 新活动累计 `14/40` 次 HTTP；自动门槛通过后即停止，不继续消耗剩余预算。
- 已生成窗口级 A/B、答案表和两段本地音频；同一窗口内全部 cue 使用同一盲评版本。人工结论未填写前不得升级发布状态。
- 公开报告只含模型 ID、哈希、计数、状态和问题码，声明不含字幕正文、密钥或绝对路径。
- 全量回归：`556 collected / 556 passed`；相关 Ruff、CI 策略、项目 smoke 与 `git diff --check` 通过。
- 项目 smoke 验证基础导入、两项 self-test、Pipeline 只读命令、首页及 diagnostics HTTP 200；现有 Pipeline review 的 188 项问题和一个既有文件权限 warning 均按原样保留。
- ASR 与 ASS 未参与本轮实现或评测；`asr_experiment_mode=off`，Preview 继续隐藏，内置 Profile 和生产默认不变。
