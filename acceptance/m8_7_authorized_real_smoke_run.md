# M8.7 Authorized Real Sample Smoke Run — Acceptance

## 1. What M8.7 Does

M8.7 的目标是在用户明确授权一个具体本地样本路径后，执行一次真实小样本 smoke run，收集性能和 routing 行为证据，并生成可审计的 redacted acceptance summary。

M8.7 不默认开启 routing，不改核心算法，不提交真实媒体、SRT、JSON report、transcript payload 或 output/work 产物。

## 2. Authorized Sample Policy

M8.7 严格遵循 "授权后才执行" 原则：

- 不自动从 `work/` 选取文件
- 不运行 Whisper 除非收到 `AUTHORIZED_SAMPLE_PATH=<path>`
- 不运行 FFmpeg 除非已授权
- 停止并报告如果无授权路径

本次授权由用户显式提供，格式符合要求。

## 3. Authorized Sample Path

```text
work\<authorized_16k_wav_sample>
```

文件大小：约 179 MB（16 kHz mono WAV，推算时长约 93 分钟）。

路径已按用户要求记录，完整文件名在 acceptance 文档和 audit 包中红action。

## 4. Smoke Scenarios Run

三个场景全部执行：

| # | Scenario | Parameters |
|---|----------|------------|
| 1 | Non-strict apply | `--segment-asr-routing apply --segment-routing-window-seconds 120 --segment-routing-max-windows 20` |
| 2 | Strict guardrail failure | `--segment-asr-routing apply --segment-routing-window-seconds 120 --segment-routing-max-windows 1 --segment-routing-strict` |
| 3 | Dry-run comparison | `--segment-asr-routing dry_run --segment-routing-window-seconds 120 --segment-routing-max-windows 20` |

## 5. Exit Code and Runtime

| Scenario | Exit Code | Runtime | Observation |
|----------|-----------|---------|-------------|
| 1 Non-strict apply | 1 | 1.5 s | 快速失败，未进入转写阶段 |
| 2 Strict guardrail | 1 | 1.5 s | 快速失败，未进入转写阶段 |
| 3 Dry-run | 1 | 1.4 s | 快速失败，未进入转写阶段 |

**注意**：三个场景的运行时间均低于 2 秒，说明失败发生在导入阶段，未实际执行音频处理或 routing 逻辑。

## 6. Apply / Fallback / Strict Behavior Observed

**实际行为**：由于底层运行环境失败，三个场景均未达到 routing 决策点。

- `apply` 未尝试（WhisperModel 导入失败）
- `fallback` 未触发（程序在导入阶段即退出）
- `strict` 模式未执行到 guardrail 检查
- `dry_run` 未执行到分析阶段

这不是 routing 代码的缺陷，而是运行环境阻止了所有场景到达业务逻辑。

## 7. Runtime Guardrail Summary

Guardrails 本身未被触发，因为程序在 `faster_whisper` -> `ctranslate2` -> `asyncio` 导入链提前失败。

失败点（全部场景一致）：

```text
transcribe.py:415  from faster_whisper import WhisperModel
  -> faster_whisper.transcribe -> import ctranslate2
  -> ctranslate2.extensions -> import asyncio
  -> asyncio.windows_events -> import _overlapped
  -> OSError: [WinError 10106] 无法加载或初始化请求的服务提供程序。
```

## 8. Coverage Summary

本次 smoke 无法评估 coverage，因为转写未开始：

- `planned_window_count`：N/A（未计算）
- `estimated_asr_calls`：N/A
- `coverage_full`：N/A
- `coverage_rate`：N/A
- `gap_count`：N/A
- `candidate_accepted`：N/A

## 9. Baseline Preservation Observation

Baseline SRT 未生成（转写未开始），因此无 baseline 需要保护。已有的工作目录文件（包括 `work/` 下的音频）未被修改、删除或覆盖。

## 10. Report Redaction Rules

本次生成的安全元数据日志包含：

- 场景名称
- 命令 shape（含路径）
- 退出码
- 运行时间
- stderr 错误摘要（无 transcript 文本）

**不包含**：
- 完整转录文本
- 完整 segment payload
- 生成的 SRT 内容
- 音频/视频内容
- 模型文件
- API Key

## 11. Generated Outputs Intentionally Not Committed

| 文件 | 位置 | 说明 |
|------|------|------|
| Smoke 日志 | `output/reports/segment_asr_routing/m8_7_*.log` | 含 stderr，不提交 |
| Smoke 元数据 | `output/reports/segment_asr_routing/m8_7_*_meta.json` | 运行时统计，不提交 |

## 12. What Remains Experimental

Segment ASR routing 的 `apply` 模式仍标记为实验性：

- 需要真实运行环境（兼容的 Python + ctranslate2）才能验证完整链路
- 本次环境失败表明在特定环境配置（Python 3.13 + 当前 ctranslate2 版本）下存在导入兼容性问题
- 未来需要验证 `apply` 在实际长音频上的 wall-clock 性能
- 需要验证 `--segment-routing-strict` 的 cap exceeded 行为在真实运行中是否按预期干净失败

## 13. Future M8.8 / M9 Direction

M8.8 可能方向：
- 解决运行环境兼容性问题（Python 3.13 + ctranslate2 的 `_overlapped` 加载）
- 或在确认环境修复后重跑 M8.7 授权 smoke
- 引入环境预检，在 Whisper 导入前诊断 ctranslate2 可用性，给出更友好的错误信息

M9 方向：
- 将 segment ASR routing 从 experimental 提升为稳定功能（如果性能 evidence 支持）
- 集成到 batch pipeline 和 Web UI
- 支持 ASS 输出预留（当前仅 SRT）

---

## Test Results

```text
tests/test_segment_asr_smoke_report.py ...............  [25%]
tests/test_segment_asr_routing_integration.py ...........  [72%]
tests/test_segment_asr_routing_runtime_guards.py ......  [87%]
tests/test_segment_asr_routing_report_ux.py .......     [100%]

58 passed in 0.65s
```

全部 58 个 targeted 测试通过，无代码改动导致的回归。

## Git Status

```text
 M .gitignore
?? acceptance/m8_7_authorized_real_smoke_run.md
?? audit/external_audit_m7_1.zip
?? audit/external_audit_m7_2.zip
?? ... (existing audit zips)
?? project_evaluation_report.md
```

- 无 staged changes
- `git diff` 仅包含 `.gitignore` 的 allowlist 新增（`!acceptance/m8_7_authorized_real_smoke_run.md`）
- `git diff --check` 输出为空（无 whitespace 错误）
- 本次 M8.7 的 source changes 仅限：
  - `.gitignore`（新增 M8.7 acceptance allowlist）
  - `acceptance/m8_7_authorized_real_smoke_run.md`（新建）
- 无代码改动

## Commit Policy

本次 M8.7 无代码改动，仅新增 acceptance 文档和 `.gitignore` 更新。按指令要求，不单独提交，等待用户进一步指令。
