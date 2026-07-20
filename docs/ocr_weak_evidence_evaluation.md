# OCR 弱标注对照评测

该工具把硬字幕 OCR、ASR 源文和模型译文作为离线证据进行时间轴对照。OCR 不是人工金标，报告中的 disagreement、coverage 和 weak screen 不等同于 CER/WER，也不能批准候选进入生产 `apply`。

## Manifest

复制 `tests/ocr_evidence/manifest.example.json` 到 gitignored 的本地目录并填写项目内相对路径。每个样本包含 OCR 双语 SRT、可选 OCR sidecar、baseline，以及零个或多个 candidate。`translation_srt` 可使用纯中文字幕或本项目生成的“源文 + 中文”双语 SRT。

```json
{
  "schema_version": 1,
  "samples": [
    {
      "id": "fr-interview",
      "language": "fr",
      "tags": ["interview", "noise"],
      "ocr_srt": "work/ocr/fr-interview.burned-bilingual.ocr.srt",
      "ocr_sidecar": "work/ocr/ocr-evidence.local.json",
      "baseline": {
        "source_srt": "output/source/fr-interview.small.srt",
        "translation_srt": "output/bilingual/fr-interview.small.bilingual.zh-CN.srt"
      },
      "candidates": [
        {
          "id": "candidate-v1",
          "source_srt": "work/candidates/fr-interview.candidate-v1.srt",
          "translation_srt": "work/candidates/fr-interview.candidate-v1.zh.srt"
        }
      ]
    }
  ]
}
```

所有输入和输出路径必须位于项目目录内。`clean_burned_subtitles.py` 的混合清洗结果使用过 ASR 证据，不能回头充当 ASR 参考。

## 运行

默认模式不访问网络：

```powershell
$env:PYTHONPATH = "src\core;src\pipeline;src\config;src\web;src\tools"
.\.venv\Scripts\python.exe -B src\tools\ocr_evidence_compare.py `
  --manifest work\ocr-evidence\manifest.local.json `
  --run-id local-screen-v1
```

只有显式选择 Provider 和正数预算后，才会对规则无法判断的片段调用 LLM：

```powershell
.\.venv\Scripts\python.exe -B src\tools\ocr_evidence_compare.py `
  --manifest work\ocr-evidence\manifest.local.json `
  --run-id local-screen-v1-judge `
  --llm-judge uncertain `
  --provider deepseek-main `
  --max-llm-cues 20
```

LLM 裁判使用随机 A/B 标签、当前片段及前后各一个时间窗口，并缓存结果。API Key 不进入报告。

## 输出与决策

每次运行写入 `output/reports/ocr_evidence/<run-id>/`：

- `summary.json`、`summary.md`：只含哈希、指标、门槛和结论，不含字幕正文或绝对路径。
- `details.local.json`：本地逐片段证据，包含字幕正文，不应提交或分享。
- `review_needed.srt`：高差异片段，便于对照画面和声音。

候选结论只有 `insufficient_evidence`、`rejected_by_weak_screen` 和 `eligible_for_gold_benchmark`。最后一种只允许继续运行冻结金标、两轮完整评测和人工听审；报告始终写明 `apply_allowed=false`。

缺少 sidecar 时工具仍可生成低置信报告，但不会把 OCR cue 纳入高稳定度晋级统计。重新运行 `extract_burned_subtitles.py --reuse-raw` 可以利用已有 `raw-ocr.local.json` 生成 sidecar，不重新调用 OCR。
