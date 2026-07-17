# 智译字幕工坊开发路线图

状态值：`planned`、`in_progress`、`completed`、`blocked`。

本文件维护长期推进目标和阶段准入条件；具体构建、测试和人工验收证据继续记录在 `acceptance/`。

## 当前基线

- 当前版本：v0.6.1 Windows External Test Preview。
- 当前阶段：阶段七，翻译质量、恢复与自动 E2E；阶段五 ASR 仍为 `no_go/off`，阶段 5.1 外部验收缺口继续保留。
- 产品边界：本地 Windows 字幕转写、翻译、质检和批处理；默认稳定输出 SRT。
- 固定约束：不静默下载大组件，不修改系统 PATH，不泄露 API Key，不破坏 CLI/Web/Pipeline 兼容性。

## 阶段一：v0.5.1 Debug 与版本收口

状态：`completed`

目标：消除已知后台线程竞态，形成可提交、可复现、测试无后台异常、验收记录可信的安装版基线。

主要工作：

- 修复任务记录在后台执行期间消失导致的 `NoneType`、`KeyError` 和未处理线程异常。
- 为任务执行增加统一异常边界、API Key 清理和日志 secret 脱敏。
- 固化文本行尾规则，消除 mixed EOL。
- 完成语法、自测、全量测试、Web smoke、unpacked/NSIS 构建和 packaged smoke。
- 更新 v0.5 验收证据、安装器大小和 SHA-256。

准入条件：v0.5 portable runtime、Electron shell 和 NSIS 构建链已经可用。

完成标准：全量测试在未处理线程 warning 视为 error 时通过；安装版首页和 diagnostics API 返回 200；默认包不含 CUDA；源码差异检查通过。

非目标：不改变 ASR 算法，不重设计 UI，不实现 ASS，不执行付费 LLM 或真实长片验收。

完成证据：

- Node、Python、PowerShell 语法检查和基础导入检查通过。
- 字幕翻译与质检自测通过。
- 全量 `384 passed`，并将 `PytestUnhandledThreadExceptionWarning` 提升为 error 验证。
- 源码 Web smoke：首页与 diagnostics API 均返回 200。
- 默认 CPU/auto unpacked 与 NSIS 构建成功；packaged smoke 确认 `runtime_layout=packaged`、`python_source=packaged-python`、FFmpeg 来自 packaged environment，且未携带 CUDA。
- Electron 退出后验证端口关闭，未遗留后端进程树。
- 安装器：`desktop/release-validation/智译字幕工坊 Setup 0.5.0.exe`。
- 安装器大小：`264834595` bytes（`252.6 MB`）。
- SHA-256：`EC827C1FB9F4EE8C7F74EF845FFA8A83CBED8299847A926A0DF0C37F0D34236B`。
- `git diff --check` 通过，`desktop/main.js` 和 `src/web/web_server.py` 均为 LF，无 mixed EOL。
- 未执行真实长片、模型下载、付费 LLM API、GPU 包或干净 VM 安装验收；这些项目保留在后续阶段。

## 阶段二：外测安装器与第一轮 UI

状态：`completed`

目标：交付无需命令行操作的 Windows 外测版，并完成以可用性为中心的第一轮 UI 优化。

主要工作：CPU/auto 与 GPU 构建矩阵、正式图标与版本 manifest、干净 Windows VM 安装/卸载验证、统一加载/空状态/错误状态、任务总体进度和设置流程。

准入条件：阶段一完成，安装版 Debug 基线稳定。

完成标准：无系统 Python/Node/FFmpeg 的 VM 可安装启动；CPU/GPU 短样本验收通过；错误均有脱敏、可操作诊断。

例外关闭说明：2026-07-10 决定将干净 Windows VM/测试机的安装、启动和卸载验收延期，阶段二按本机自动化、CPU/GPU 构建、packaged smoke 与短样本证据例外关闭。未执行的 VM 清单不视为通过，v0.6 仍是 External Test Preview；在正式稳定版发布或对外宣称“零配置兼容”前必须恢复并完成该验收。详见 `acceptance/v0_6_stage2_windows_external_test.md`。

非目标：不实现自动更新、TTS、配音、声线克隆或口型同步。

## 阶段三：ASR 评测基线

状态：`completed`

目标：建立可量化、可复现的 ASR 优化评测体系，不改变默认生产算法。

主要工作：固定多语言和复杂声学短样本集，记录 CER/WER、漏句率、重复率、时间轴偏移、CPU/GPU 耗时与资源峰值，并纳入 segment routing 报告。

实施说明：`docs/stage3_asr_benchmark.md`。

完成证据：`acceptance/stage3_asr_benchmark_closeout.md`。

准入条件：外测构建和诊断环境稳定；阶段二干净 Windows VM 验收按上述例外延期，不阻断本阶段，但不能据此宣称安装兼容性已经通过。

完成标准：当前算法形成冻结 baseline，任一候选优化都能自动产生可比较报告。

非目标：实验结果不直接覆盖正常字幕，不默认启用新路由策略。

## 阶段四：架构拆分与 Debug 基础设施

状态：`completed`

目标：降低 Web 和 Pipeline 主模块复杂度，为 ASR、UI 和格式扩展建立稳定边界。

主要工作：继续拆分 Provider/Profile、Storage API；将抽音频、ASR、翻译、质检、归档提取为独立 stage；结构化任务日志并提供脱敏诊断包。

准入条件：阶段一回归基线和阶段三评测基线可用。

完成标准：现有 HTTP API、CLI、恢复语义兼容；各 stage 可独立测试；主模块只保留分发和调度。

非目标：不借重构改变用户可见行为。

完成证据：`acceptance/stage4_architecture_debug_closeout.md`。

## 阶段五：ASR 算法正式优化

状态：`in_progress`

目标：以评测证据驱动参数自适应、幻觉抑制、混合语言路由、局部重试和时间轴优化。

主要工作：所有新策略支持 `off/dry_run/apply`，保留原始证据，失败时回退完整原始 ASR，并通过 Language Profile 管理已验证策略。

准入条件：ASR stage 已独立，baseline 和回滚路径完整。

完成标准：核心指标有可复现改善，且漏句率、时间轴和资源成本无不可接受退化。

非目标：不凭单一样本或主观观感切换默认算法。

2026-07-15 进展：已补齐混合语言三指标接口、原子 checkpoint/resume、routing 逐样本超时、匿名人工听审包和独立 Go/No-Go 汇总。现有 `local-retry-selective-v2` 与 `mixed-route-v1` 均为 `no_go`，没有候选进入完整两轮矩阵；生产默认保持 `off`。执行说明见 `docs/stage5_asr_closeout.md`，证据见 `acceptance/stage5_asr_optimization_progress.md`。

2026-07-16 进展：新增 OCR 弱标注离线对照工具，以纯时间轴多对多对齐比较硬字幕 OCR、baseline 和候选 ASR/译文；默认零网络调用，可选有预算的随机 A/B LLM 裁判。弱标注只允许把候选标记为 `eligible_for_gold_benchmark`，不能改变既有 `no_go` 或批准生产 `apply`。使用说明见 `docs/ocr_weak_evidence_evaluation.md`。

## 阶段 5.1：安全与发布基线收口

状态：`in_progress`。本专项位于阶段五与阶段六之间，采用发布阻断优先顺序，详见 [`project_hardening_plan.md`](project_hardening_plan.md)。

准入顺序：localhost 会话安全与配置损坏保护 → `VERSION`/Git/CI 基线 → 干净 Windows 10/11 VM 验收 → 阶段五证据补齐或阶段六 UI。未完成 VM、真实媒体人工听审的项目必须保持未通过，不得用本机或合成证据替代。

阶段五现有结论继续为 `no_go`，生产默认 `asr_experiment_mode=off`；本专项不批准候选 `apply`，也不实现 ASS。

## 阶段六：任务聚焦型 UI 深度改造

状态：`completed`

例外进入说明：2026-07-16 用户明确决定暂不推进 ASR 优化并进入阶段六。该决定不改变阶段五候选 `no_go`、生产 `asr_experiment_mode=off`，也不把阶段 5.1 的干净 Windows VM、无驱动 GPU 或 packaged Electron 外部验收标记为完成。

目标：在后端接口稳定后统一视觉系统和任务信息架构。

主要工作：采用方案 A“控制室精修”，以中文为第一界面语言，重整高级参数层级，增加桌面侧边/窄屏全屏任务详情、ASR routing 摘要、只读字幕预览和人工复核入口，优化批量任务筛选与恢复；语言切换仅预留明确入口，不提前开放不完整英文界面。

准入条件：Pipeline 和 ASR 接口稳定。

完成标准：常见分辨率、键盘导航、颜色对比、文本溢出和 Electron 截图验收通过。

非目标：保持单文件前端交付，不引入 npm、CDN 或前端构建链。

2026-07-17 收口：全量 `541 passed` 与项目 smoke 通过；Playwright 在 1440×900、390×844 验证无横向溢出、抽屉焦点进入、Esc 焦点返回和移动端全屏详情。阶段六按本地范围完成；阶段 5.1 的 Windows VM、无驱动 GPU 与 packaged Electron 外部验收继续保持未完成。

## 阶段七：翻译质量与恢复、批量体验和自动 E2E

状态：`in_progress`

2026-07-17 稳定化：暂停继续投入 ASR 与翻译质量候选，生产默认继续保持 `off`。v0.6.1 已收拢 portable `.venv` 配置诊断、启动/断连稳定性和连续全量回归；Windows 发布进一步取消 CPU/GPU 双包，统一为内置 CPU 与 CUDA 运行时、启动后自动选择设备的单一离线安装器。未完成的干净 Windows VM 与人工质量验收不因此视为通过。

2026-07-16 进展：已以默认 `off`、非默认 `preview` 实现自适应拆分恢复、阻断翻译修复、原子缓存/成品和 CLI/Profile/Web/Pipeline 接口。真实 LLM 固定 24 cues 两轮自动文本阻断门槛通过，但原片匿名 A/B 听审发现跨 cue 语义割裂和重复，人工结论为 `no_go`。因此不在 Web 高级设置和 Profile 编辑器公开开关，下一轮评估相邻源文+译文上下文与固定时间轴的小窗口联合重译。详见 `docs/translation_reliability_preview.md` 与 `acceptance/translation_reliability_preview.md`。

2026-07-17 进展：已实现固定 ID/时间轴的小窗口联合重译、严格窗口 ID 校验、相邻重复/包含退化门槛和缓存失败原子回滚；新增完全离线 ASR/LLM 替身 E2E，覆盖输入到质检报告、阶段事件、失败恢复及 `retry-failed`。本轮未调用真实 LLM，Preview 仍为 `no_go/off`，Web/Profile 开关继续隐藏。

2026-07-17 真实评测：生产与验证工具已共用小窗口规划，schema v2 按窗口统一随机化匿名 A/B，并增加结构、重复、预算和未解决窗口自动门槛。`deepseek-main / deepseek-v4-flash` 固定 24-cue 两轮矩阵中，第一轮接受 2/2 窗口，第二轮因候选回显源文拒绝 2/2 窗口；授权活动累计 `20/20` 次 HTTP，自动结论为 `no_go`，因此未生成人工 A/B。生产默认、隐藏开关与内置 Profile 均未改变，ASR 继续保持 `no_go/off`。

2026-07-17 质量链评测：已增加 Flash 候选、原因驱动纠正、Pro 质量候选和只选择标签的独立判定器，Provider 可选配置质量模型。schema v3 固定样本两轮均接受 2/2 窗口，阻断项归零且结构、重复稳定，使用 `14/40` 次 HTTP；自动门槛通过并生成窗口级 A/B 与音频，人工盲评仍为 `pending`。在人工结论前，发布继续 `no_go`，生产默认和隐藏开关不变。

目标：提高长片和多文件任务的翻译质量与恢复能力，并建立可自动运行的完整流水线验证。

主要工作：失败条目细粒度重试和降级、文件级总体进度、滚动译文上下文、语义审校与显式可选润色、stub ASR/LLM 离线 E2E、显式可选的真实模型/API E2E。翻译质量借鉴方案见 `docs/translation_quality_wenyi_adaptation.md`。

准入条件：stage 边界和 UI 任务模型稳定。

完成标准：单条翻译失败不重做整个成功 batch；增强翻译流程通过固定字幕集 A/B 评测且默认不增加付费调用；离线 E2E 覆盖 input 到质量报告；`retry-failed` 语义不变。

非目标：普通 CI 不下载模型、不调用付费 API。

## 阶段八：ASS 正式实现

状态：`planned`

目标：通过统一字幕模型和 renderer 正式输出 ASS，同时保持 SRT 默认兼容。

主要工作：实现 ASS renderer、内置样式、Language Profile 样式绑定、CLI/Web/Pipeline 共用输出规划，以及转义、字体、时间轴和 libass smoke。

准入条件：前序稳定性、架构和 E2E 阶段完成。

完成标准：请求 ASS 时生成非空可解析文件，不再返回 reserved message，SRT 路径和内容不回归。

非目标：ASS 样式不进入 Provider，不把格式逻辑写入 Web 或 Pipeline 主流程。
