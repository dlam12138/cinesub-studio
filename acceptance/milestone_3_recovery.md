# Milestone 3 Acceptance: 失败恢复与完成跳过

日期：2026-07-01

## 验收结果

- `pytest` passed: 22 passed
- `scripts/dev_check.ps1` passed
- Web smoke passed: home=200, diagnostics=200
- `web/index.html` script syntax check passed

## 已完成能力

- `--retry-failed` 只重试 `status == "failed"` 的任务。
- `--retry-failed` 不扫描 input，不加入新文件，不重置 `completed`、`pending` 或 `running`。
- retry helper 返回结构化结果：`reset_count`、`untouched_count`、`selected_task_ids`。
- completed 跳过规则改为：状态为 `completed` 且当前配置对应最终产物存在、非空。
- stage reuse 继续复用已有且有效的 audio、source SRT、translated/bilingual SRT 和 quality report。
- stale/running-after-crash 只作为 warning 展示，不自动 reset。
- `/api/pipeline/progress` 新增恢复字段：`recoverable_failed_count`、`can_retry_failed`、`stale_running_count`、`recoverable`、`recovery_action`。
- Web 进度区显示失败可重试提示、stale warning，并根据 `can_retry_failed` 控制“重试失败”按钮状态。

## 固定 recovery_action 枚举

- `none`
- `retry_failed`
- `skip_completed`
- `reuse_outputs`
- `stale_running_warning`
- `not_recoverable`

## QA 资料

- 本轮截图保存在 `acceptance/screenshots/milestone_3_recovery.png`。
- `acceptance/screenshots/` 是本机 QA 资料目录，不提交到 Git。

## 注意事项

全量 `ruff check src tests` 仍会暴露一批既有历史 lint 问题；本轮按项目验收入口 `scripts/dev_check.ps1` 验证通过。
