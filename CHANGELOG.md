# Changelog

本文件只记录面向用户的显著变化，不等同于完整 Git 提交历史。

## Unreleased — v0.7.x candidate

### Added

- 更完整的长片任务生命周期、任务历史和诊断信息。
- Web 重启后的后台 Pipeline 状态恢复与 stale/running 警告。
- 更严格的输入、配置、模型位置和断点续跑身份校验。
- 公共金标 ASR 评测、匹配对照、bootstrap 和匿名审查工具。
- 更安全的诊断包、日志脱敏和本地数据边界。

### Changed

- 批量任务的计划、运行和失败重试共享同一配置解析与预检路径。
- 失败恢复只选择明确失败的任务，不自动重置 stale/running 或扫描新文件。
- `balanced` 和 `quality` 继续只执行 selective retry dry-run，不自动应用候选文本。

### Fixed

- 修复后台 worker 交接、异常终态记录和 Windows 子进程树清理中的可靠性问题。
- 修复不同 ASR cue 边界下盲评窗口无法严格按时间对齐的问题。
- 修复重切分处理零时长词片段时可能触发文本守恒回退的问题。

### Quality-default boundary

Quality evaluation did not identify sufficient evidence to change the current
ASR model split or translation defaults. The release candidate therefore keeps
the existing quality defaults while delivering reliability, diagnostics, and
evaluation improvements.

- `large-v3` 未晋级为新的默认模型。
- 本轮重切分候选被拒绝。
- selective retry `apply` 未设为默认。
- `three_pass` 未晋级为默认翻译策略。
- 不宣称 ASR 或翻译质量显著提升。

### Release status

- Target version: not yet fixed beyond `v0.7.x candidate`.
- Portable candidate build: pending an explicit target version.
- Tag: not created.
- GitHub Release: not created.
- Release approval: pending separate final acceptance.
