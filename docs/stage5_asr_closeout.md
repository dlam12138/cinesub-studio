# 阶段五 ASR 收口执行说明

本阶段继续遵循 `off / dry_run / apply` 边界。候选只有在固定语料初筛、两轮完整矩阵和五类真实媒体人工听审全部通过后，才允许显式 `apply`；内置生产 Profile 始终保持 `off`。

## 1. 冻结执行基线

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\tools\capture_stage5_baseline.py `
  --manifest tests\asr_benchmark\manifest.local.json `
  --manifest tests\asr_challenge\local\manifest.local.json `
  --test-summary "<本轮全量测试结果>"
```

快照写入 `output/reports/stage5-closeout/`，包含 Git 状态、语料 fingerprint、Python、FFmpeg、CUDA、驱动和本地模型摘要。该文件属于本机运行证据，不提交。

## 2. 混合语言指标与人工标注

Benchmark 每个 run 新增 `code_switch_metrics`：

- `mer`
- `post_switch_first_token_error_rate`
- `language_span_recall`

缺少逐时段语言金标或 hypothesis 语言证据时，相应指标为 `null` 并携带 warning，不从文件名或全局语言检测结果推断语言段。

生成本地标注模板：

```powershell
.\.venv\Scripts\python.exe -B src\tools\prepare_code_switch_annotations.py `
  --manifest tests\asr_challenge\local\manifest.local.json `
  --output-dir output\reports\asr_benchmark\stage5-closeout\language-annotations
```

人工填写有序、互不重叠的 `start/end/language` 后，再把对应文件通过本地 benchmark manifest 的 `language_annotations` 字段关联到样本。字幕正文和标注文件只留在忽略目录。

## 3. 可恢复初筛

`--run-id` 为 checkpoint 提供稳定标识；`--resume` 只复用 signature、corpus fingerprint、配置、候选和 repeat 数完全一致的完成项。JSON 采用临时文件加 `os.replace()` 原子写入。routing 可设置逐样本超时，超时结果会保存为失败证据，不影响已完成样本。

```powershell
.\.venv\Scripts\python.exe -B src\tools\asr_benchmark.py `
  --manifest tests\asr_challenge\local\manifest.local.json `
  --config large-v3-cuda-float16 `
  --candidate local-retry-selective-v2 `
  --repeat 3 `
  --run-id stage5-local-retry-v2-screen

.\.venv\Scripts\python.exe -B src\tools\asr_benchmark.py `
  --manifest tests\asr_challenge\local\manifest.local.json `
  --config large-v3-cuda-float16 `
  --routing-only --routing-timeout-seconds 900 `
  --run-id stage5-mixed-route-screen
```

中断后追加 `--resume`。只有总体 CER 相对改善至少 5%、目标子集至少 10%、局部重试增量耗时不超过 25%，且漏句、重复和时间轴指标不退化时，才运行两轮完整矩阵。

## 4. 匿名人工听审

```powershell
.\.venv\Scripts\python.exe -B src\tools\prepare_stage5_review_pack.py --initialize
```

编辑生成的 `output/reports/stage5-review/review_manifest.local.json`，为五类样本填写已核验的 60–90 秒区间、baseline SRT 和 candidate SRT。现有媒体未验证自然混合语言样本，因此模板明确保留缺口，禁止以合成样本替代。

```powershell
.\.venv\Scripts\python.exe -B src\tools\prepare_stage5_review_pack.py `
  --manifest output\reports\stage5-review\review_manifest.local.json
```

先盲评 `subtitle_A.srt` 与 `subtitle_B.srt`，填写 `review_form.json` 后再查看 `private_mapping.json`。记录漏句、重复、错语种、切换点首词、时间轴、可读性和总体偏好。

## 5. Go/No-Go

```powershell
.\.venv\Scripts\python.exe -B src\tools\stage5_go_no_go.py `
  --baseline <冻结 baseline.json> `
  --candidate-report <候选第一轮.json> `
  --candidate-report <候选第二轮.json> `
  --routing-report <mixed-route dry-run.json> `
  --manual-review output\reports\stage5-review\review_form.json `
  --output-dir output\reports\asr_benchmark\stage5-closeout\decision
```

初筛失败允许只传一份候选报告并直接输出 `no_go`；初筛通过但缺少第二轮时输出 `requires_second_full_round`；自动门槛通过但人工听审未完成时输出 `pending_manual_review`。只有 `decision=go` 时 `apply_allowed=true`，工具本身不会修改候选注册表或 Profile。
