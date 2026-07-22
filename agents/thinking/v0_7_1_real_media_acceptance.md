# v0.7.1 Real Media Acceptance

- 时间：2026-07-22
- 用户目标：使用三个确认的匿名真实媒体片段完成 v0.7.1 验收，提交匿名结果并推送验收分支。

## 已知事实与证据

- 三个样本覆盖低音量对白、清晰对白和远场舞台语音，时长分别为 292、300 和 300 秒。
- small 与 large-v3 均以 CUDA/float16、`local_files_only=True` 完成加载预检。
- VideOCR CLI v1.5.1 使用本地 PaddleOCR 生成法语硬字幕弱证据；原始 release archive 未提供，因此只冻结了可执行文件哈希。
- Attempt 2 暴露零时长法语词片段导致的重切分文本守恒回退；Attempt 3 的 apply 接受窗口为 0；Attempt 4 验证 `quality=dry_run` 发布候选。

## 本轮决策摘要

- 最终结论为条件通过，`quality` 默认 retry 从 `apply` 降为 `dry_run`。
- 显式 apply 接口继续保留，但自动替换未通过真实媒体验收，不作为默认发布行为。
- 热词隔离没有改善冻结专名，不设置默认热词。

## 实际执行的操作

- 完成正式 VideOCR 弱证据、Attempt 2/3/4 主矩阵、旧 CLI 回归和热词 A/B/C/D 隔离。
- 修复重切分对零时长文本词片段的丢弃问题，并完成两次 remediation commit。
- 生成允许提交的匿名 Markdown、JSON 和两份 CSV 报告。

## 验证结果

- 最终 12 次独立冷进程运行全部成功。
- 所有最终 SRT 时间轴有效；9 次启用重切分的运行全部应用成功且无守恒回退。
- `quality` 最终稳定为 dry-run，VAD uncovered 没有自动 apply。
- 旧 CLI 与 speed 输出逐字节一致。
- 全量 pytest、导入检查、两个 self-test、Web smoke、两个 HTTP 200、Electron JavaScript 检查和 `git diff --check` 均通过。

## 未解决问题与下一步

- 自动 apply 仍缺少至少 3 个 accepted window 的真实人工审核样本，不能恢复为 preset 默认值。
- VideOCR 没有本轮自建的逐帧 temporal-stability sidecar，因此 OCR 只作为低权重弱证据。
- 真实媒体、完整字幕、OCR 输出和本地路径继续保留在 Git 忽略目录，不得提交。
