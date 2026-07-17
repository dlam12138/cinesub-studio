# 阶段四：架构拆分与 Debug 基础设施验收摘要

验收日期：2026-07-11
状态：`completed`

## 架构边界

- Web 已拆出 Provider/Profile、Storage、ASR evidence、文件检查、Runtime 和 Pipeline API 模块；`web_server.py` 保留 HTTP 路由、请求解析、通用响应和模块分发。
- Pipeline 已拆出 CLI parser、effective config、状态/Review 展示、共享任务状态、输出规划、五类 stage 和结构化事件日志。
- `TaskState`、`RetryPlan`、失败任务选择、completed 产物校验和 Web 恢复提示共用 `task_state.py`；Web 不再维护独立 retry 判断。
- 抽音频、ASR、翻译、质检、归档统一使用 `TaskContext`、`StageResult`、`StageError`，同时保留原 CLI、输出和恢复行为。

## Debug 能力

- 保留 `logs/pipeline.log`，新增 `logs/pipeline.events.jsonl`，记录 started/completed/reused/failed、耗时、returncode、错误类别和脱敏摘要。
- 新增 `POST /api/runtime/diagnostic-bundle`；并发生成返回 409。
- 新增受限下载 `GET /api/runtime/diagnostic-bundle/download?file=<name>`，只接受诊断目录内固定命名 ZIP。
- 诊断包包含 runtime diagnostics、应用信息、脱敏 Provider 元数据、任务状态和截断日志；不包含媒体、字幕正文、翻译缓存、API Key 或用户绝对路径。
- ZIP 写入前后均执行 secret/path 安全扫描。

## 兼容与验收

- `POST /api/jobs`、`GET /download`、Provider/Profile、Pipeline、runtime diagnostics 和 ASS reserved 行为保持兼容。
- `retry-failed` 只选择 failed；completed/pending/running 不重置；stale running 只提示 warning。
- 独立测试覆盖五类 stage、空产物、子进程失败、归档冲突、中文路径、共享恢复、诊断包 201/409/受限下载和安全扫描。
- 全量测试：`431 passed`，并将 `PytestUnhandledThreadExceptionWarning` 视为 error。
- 基础导入、字幕翻译自测、质检自测、Pipeline scan/status/review、Web 首页、diagnostics API、诊断包 API、Python 编译和 `git diff --check` 已执行。
- Web 实测：首页 200、diagnostics 状态 `ok`、诊断包创建成功、受限下载 200。
- `--review` 正常读取本地 8 份历史质量报告；因报告内存在 8 个质量错误按既有语义返回 1，不属于程序回归。

## 非目标与后续

- 未实现 ASS、未改变默认 segment routing、未自动切换 ASR 参数。
- 阶段五只能基于阶段三冻结语料比较候选策略，并继续遵循 `off/dry_run/apply`、保留原始证据和失败回退约束。
