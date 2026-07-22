# 轮次记录：v0.7.0 Controlled ASR Quality Loop

- 时间：2026-07-22
- 用户目标：实施 v0.7.0 Controlled ASR Quality Loop，在现有单模型 faster-whisper 链路中加入词级时间戳、确定性重切分、固定配方局部 retry、配置优先级、报告 schema 和模型预检。

## 已知事实与证据

- 当前产品链路仍以 faster-whisper 为唯一 ASR 后端。
- 旧治理约束禁止通用 ASR candidate、segment routing、局部重跑和输出替换；本轮已调整为禁止通用框架，但允许固定配方 `local-retry-selective-v2` 的受控局部 retry。
- `asr_strategy.py` 继续作为离线研究资产保留，产品运行链路不直接导入其中的 candidate registry、`candidate_id` 或 `mixed-route-v1`。

## 本轮决策摘要

- v0.7 公共接口只暴露 `quality_preset`、`word_timestamps`、`resegment_subtitles`、`asr_retry_mode` 和 `asr_hotword_prompt`，不暴露 candidate ID。
- 有效配置优先级固定为：显式 CLI/Web 参数 > `quality_preset` 展开值 > Language Profile > 默认值。
- 处理顺序固定为：首次 ASR、原始 cue 诊断、规划 retry windows、候选识别、dry-run 或事务 apply、最终重切分、写 SRT 与报告。
- retry 门槛包含预算、硬拒绝、改善评分和整片时间轴校验；报告只写指标、hash 与长度，不写完整 transcript。

## 实际执行的操作

- 修改治理与用户文档：`AGENTS.md`、`README.md`。
- 修改配置和 profile：`src/config/language_profile_store.py`、`src/pipeline/pipeline_config.py`、`src/pipeline/batch_worker.py`、`src/pipeline/pipeline_cli.py`、`src/pipeline/pipeline_stages.py`、`src/pipeline/task_state.py`。
- 修改 ASR 核心：`src/core/asr_runtime.py`、`src/core/transcribe.py`。
- 新增产品化模块：`src/core/asr_retry.py`、`src/core/subtitle_resegment.py`。
- 修改 Web/API：`src/web/job_api.py`、`src/web/pipeline_api.py`、`src/web/web_server.py`、`src/web/asr_model_api.py`、`web/index.html`。
- 保留并兼容已有模型定位与诊断改动：`src/tools/asr_model_locator.py`、`src/tools/runtime_env.py`。
- 新增和更新测试：`tests/test_asr_quality_loop.py`、`tests/test_asr_model_http.py`、`tests/test_asr_modes.py`、`tests/test_premium_ui_refresh.py`，并保留旧离线 `asr_strategy` 测试。

## 验证结果

- `pytest -q` 通过。
- 导入检查通过：`transcribe`、`subtitle_translate`、`quality_checker`、`batch_worker`、`web_server`、`download_model_file`、`runtime_env`、`runtime_paths`、`subtitle_model`、`runtime_api`、`pipeline_api`。
- `subtitle_translate.py --self-test` 通过。
- `quality_checker.py --self-test` 通过。
- `start_web.ps1 -Smoke -NoBrowser -NonInteractive` 通过。
- `/` 和 `/api/runtime/diagnostics` 在短生命周期本地后端中均返回 200。
- `node --check desktop/main.js`、`desktop/preload.js`、`desktop/launch.js` 通过。
- `git diff --check` 通过，仅提示既有 LF/CRLF 规范化信息。

## 未解决问题与下一步

- 未执行真实模型 ASR、真实 `large-v3` 模型加载或真实媒体局部 retry；自动化测试使用 mock 与合成 artifact，避免下载大模型或处理用户媒体。
- 未执行正式 Release 构建；本轮未改变 Electron 0.6.2 启动契约、资源目录或发布文件名。
- 后续可用真实短样本人工验证 `quality` 模式下的模型预检、局部 retry 报告和重切分视觉效果。
