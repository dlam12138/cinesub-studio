# 阶段 5.1 安全与发布收口验收

状态：`in_progress`。

本记录只登记实际执行证据。代码门槛、自动回归结果在本轮验证后补录；以下外部环境项目尚未执行：

## 2026-07-15 本机自动验证

- 全量 pytest：`469 passed`。
- 基础导入、字幕翻译 self-test、质量检查 self-test：通过。
- Pipeline `scan`、`status`：只读执行完成；`review` 正确报告现有报告中的质量 warning/fail，因此不登记为质量通过。
- Web 首页与 `/api/runtime/diagnostics`：HTTP 200。
- 会话 smoke：无令牌写请求 403，带令牌写请求成功；CSP 响应头存在。
- Node 语法、版本一致性、增量 Ruff、密钥/运行产物策略扫描与 `git diff --check`：通过。
- `start_web.ps1 -Smoke -NoBrowser -NonInteractive`：通过，未下载模型或处理媒体。

全仓历史 Ruff 当前仍有既有债务；CI 对本专项新增安全/恢复/版本代码执行 Ruff，全仓清理不在本轮改动边界内。

- [ ] 干净 Windows 10 VM CPU 安装/启动/卸载。
- [ ] 干净 Windows 11 VM CPU 安装/启动/卸载。
- [ ] GPU 包缺驱动诊断。
- [ ] packaged Electron sandbox、文件夹选择、外链与进程树退出。
- [ ] 真实混合语言媒体人工听审。

当前 ASR 结论仍为 `no_go`，默认 `asr_experiment_mode=off`。未执行项不得登记为通过。

## 2026-07-16 失败路径补强

- 全量 pytest：`475 passed`。
- 新增覆盖：错误令牌、合法同源、畸形 JSON、multipart boundary、配置源不可读、备份失败、原子替换失败、HTTP 恢复鉴权与脱敏、版本失配、Electron sandbox 与仅 HTTPS 外链。
- 项目 `scripts/smoke_test.ps1`：通过；首页与 diagnostics 均为 HTTP 200，导入、两项 self-test、运行环境诊断和 Pipeline 只读检查均完成。
- Pipeline review 仍如实报告现有 `188` 个问题（8 errors、180 warnings），不登记为字幕质量通过，也未修改用户媒体或字幕。
- 版本 `0.6.0`、CI 策略扫描、增量 Ruff、Node 语法、workflow YAML 和 `git diff --check`：通过。
- 修复了两类安全失败行为：配置备份源不可读不再落入通用 500；畸形 JSON 不再被路由当作空对象继续执行。
