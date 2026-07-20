# 项目加固与发布收口推进计划

状态：`in_progress`
版本基线：`0.6.1`（External Test Preview）
执行原则：发布阻断优先；不清理、不覆盖、不自动暂存或提交既有工作区成果。

## 目标与边界

本计划作为阶段五与阶段六之间的阶段 5.1 专项。先关闭 localhost 安全、配置损坏保护、版本和 CI 基线，再执行干净 Windows 10/11 VM 验收。只有这些门槛通过后，才继续补齐阶段五真实媒体证据或冻结接口进入阶段六。

本专项不修改 ASR 候选策略，不批准 `apply`，不创建 optimized Profile，不实现 ASS，不下载模型、不调用付费 API、不重建 `.venv`、不修改系统 PATH、不处理用户媒体。

## 工作流与准入门槛

### H1 本地 Web 安全加固

- [x] 每个后端进程生成随机内存会话令牌，并由 `GET /api/session` 提供给当前本地页面。
- [x] 所有 POST/PUT/DELETE 校验 `X-CineSub-Token`；失败返回 403 与 `invalid_local_session`。
- [x] Host 与 Origin 限定为当前 `127.0.0.1:<port>`；JSON 与受支持 multipart 严格区分。
- [x] 前端统一拦截写请求、自动取得令牌；GET、下载和 CLI 只读接口不改变。
- [x] 响应增加 CSP、`nosniff`、frame deny 和 no-referrer。
- [x] Electron 外链只允许 `https:`，BrowserWindow 已启用 sandbox；需随 packaged smoke 再验证文件夹选择和退出流程。

### H2 Provider/Profile 配置恢复

- [x] 已存在但不可读的配置进入 `config_error`，写操作不再把它当作空配置覆盖。
- [x] `GET /api/config/status` 只返回 store、状态和脱敏错误。
- [x] `POST /api/config/recover` 只接受 `backup_and_reset`；先保存原始字节，成功后才原子重置。
- [x] 有效配置和未配置状态拒绝恢复；备份失败不修改原文件。
- [x] Language Profile 本地覆盖损坏时，内置 Profile 仍可只读使用。
- [x] 页面显示明确恢复说明并要求确认；备份文件和配置正文不进入 Git。

### H3 发布与版本基线

- [x] 根目录 `VERSION` 是唯一权威版本，当前为 `0.6.1`。
- [x] Web app-info 从 `VERSION` 读取；Python 元数据、Electron 包和 lockfile 版本必须一致。
- [x] portable 与 installer 构建在开始时执行版本一致性检查，不一致立即失败。
- [x] 已建立 Git 基线清单；用户确认前不 stage/commit。
- [ ] 用户确认源码、品牌资产和设计元数据的最终提交边界。

### H4 CI 与外测

- [x] Windows Python 3.12 CI 已覆盖 pytest、增量 Ruff、导入、自测、Node 语法、Web smoke、版本、密钥/运行产物扫描和 `git diff --check`；全仓历史 Ruff 债务另行收口。
- [x] CI 强制离线模型环境，不调用 LLM、不要求 CUDA。
- [ ] 干净 Windows 10 VM：CPU 安装、启动、目录选择、配置、退出进程树、卸载与用户数据保留。
- [ ] 干净 Windows 11 VM：同上。
- [ ] GPU 包：至少完成无驱动机器上的可诊断失败验证。
- [ ] Electron sandbox packaged smoke：外链、文件夹选择、关闭后的端口和进程树。

截至 2026-07-16，本地代码与自动化门禁已收口：475 项 pytest、项目 smoke、版本/策略/增量 Ruff/Node/YAML/diff 检查通过。阶段 5.1 继续保持 `in_progress`，仅由上述 VM、GPU 和 packaged Electron 外部验收阻断。

## 公共接口

- `GET /api/session`
- 所有状态变更请求头：`X-CineSub-Token`
- `GET /api/config/status`
- `POST /api/config/recover`，正文仅含 `store` 与 `action=backup_and_reset`
- 稳定错误码：`invalid_local_session`、`invalid_origin`、`config_corrupt`、`config_recovery_failed`

现有 GET/下载、Provider/Profile 数据结构、Pipeline 恢复、SRT 命名与 ASS reserved 行为保持兼容。

## 测试矩阵

自动测试应覆盖无令牌、错误令牌、错误 Host、跨 Origin、`text/plain`、JSON、multipart、令牌不落日志；配置损坏、备份失败、恢复成功、有效配置拒绝、Key 不泄漏；三个版本消费者一致和构建不一致失败。回归包括全量 pytest、导入、两项核心自测、Pipeline scan/status/review、首页与 diagnostics 200。

VM、真实 GPU 和真实媒体人工听审只记录实际结果。未执行项保留为待办，不能由本地 smoke 或合成样本替代。

## 决策规则

安全、配置、版本、CI 和 VM 门槛全部通过后，Pipeline/ASR 接口才允许冻结并进入阶段六。阶段五继续采用现有 `no_go`，默认 `off`；只有真实混合语言媒体到位后才补人工听审，且仍须满足既定两轮自动门槛。
