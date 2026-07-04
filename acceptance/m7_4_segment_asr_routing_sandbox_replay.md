# M7.4 Segment ASR Routing Sandbox Replay And Parameter Sweep

## Summary

M7.4 在 M7.1（segment-level ASR comparison prototype）、M7.2（offline segment ASR report analyzer）和 M7.3（golden fixtures and schema baseline）之上，新增了一个离线 sandbox replay 工具。

该工具批量读取 M7.1/M7.3 report JSON fixtures 或真实 report snapshot，复用 M7.2 analyzer，并对不同参数组合（confidence threshold、min segments）进行对比，生成可审计的 Markdown / JSON sandbox summary。

本轮继续保持 evidence-only，不接入 production pipeline。

---

## What M7.4 Adds

1. **离线 sandbox replay 工具** `src/tools/segment_asr_routing_sandbox.py`
   - 读取一个或多个 M7.1/M7.3 JSON report 文件。
   - 复用现有 M7.2 analyzer 的分类逻辑，不重复实现。
   - 支持单参数基线运行和参数 sweep（confidence threshold × min segments）。
   - 输出 Markdown summary（stdout 或文件）和 JSON summary（文件）。
   - 计算 `changed_from_baseline`，展示不同参数下 routing 建议的变化数量。

2. **测试覆盖** `tests/test_segment_asr_routing_sandbox.py`
   - 单/多 fixture 基线运行。
   - confidence threshold sweep。
   - min segments sweep。
   - `changed_from_baseline` 确定性验证。
   - Markdown/JSON 输出 schema 验证。
   - 错误输入处理（非法 sweep 值、缺失文件）。
   - CLI end-to-end 测试。
   - 验证 sandbox 复用 M7.2 analyzer 而非复制逻辑。
   - glob 路径扩展支持。

3. **验收文档** 即本文档。

---

## Why It Is Still Evidence-Only

- M7.4 只 replay 已有的 M7.2 analyzer 决策，不改变任何 production 行为。
- 不调用 Whisper、FFmpeg、LLM API、网络或 GPU。
- 输出仅用于审计和参数敏感性分析，不写入 production pipeline。

---

## How It Reuses M7.2 Analyzer

Sandbox 是薄编排层：

```
解析 CLI → 扩展输入路径 → 解析 sweep 参数
  → 对每个 setting 组合调用 M7.2 analyze_reports()
  → 收集 summaries
  → 与 baseline 比较
  → 渲染 Markdown / JSON
```

直接使用：

- `segment_asr_report_analyzer.analyze_reports()`
- `segment_asr_report_analyzer.Settings`
- `segment_asr_report_analyzer.AnalyzerInputError`
- `segment_asr_report_analyzer.CLASSIFICATIONS`

没有复制 `classify_window()` 或任何分类启发式逻辑。

---

## How It Uses M7.3 Fixtures

测试和 CLI 示例都使用 `tests/fixtures/asr_evidence/*.json` 中的 golden fixtures，包括：

- `keep_auto_runs_shape.json`
- `prefer_forced_fr_low_auto.json`
- `confidence_threshold_edge.json`
- `min_segments_edge.json`
- 以及其他 M7.3 阶段建立的 fixture

这些 fixtures 保证 sandbox 运行结果是确定性的、可复现的。

---

## CLI Examples

### 单参数基线运行

```powershell
.\.venv\Scripts\python.exe -B src\tools\segment_asr_routing_sandbox.py `
  tests\fixtures\asr_evidence\keep_auto_runs_shape.json `
  tests\fixtures\asr_evidence\prefer_forced_fr_low_auto.json `
  --confidence-threshold 0.70 `
  --min-segments 1 `
  --output-json output\reports\asr_evidence\m7_4_sandbox_summary.json `
  --output-md output\reports\asr_evidence\m7_4_sandbox_summary.md
```

### 参数 Sweep

```powershell
.\.venv\Scripts\python.exe -B src\tools\segment_asr_routing_sandbox.py `
  tests\fixtures\asr_evidence\*.json `
  --sweep-confidence-thresholds 0.60,0.70,0.80 `
  --sweep-min-segments 1,2 `
  --output-json output\reports\asr_evidence\m7_4_sandbox_sweep.json `
  --output-md output\reports\asr_evidence\m7_4_sandbox_sweep.md
```

默认行为：

- Markdown 输出到 stdout。
- JSON 仅在 `--output-json` 提供时写入。
- Markdown 文件仅在 `--output-md` 提供时写入。
- 成功返回 exit code `0`。
- 输入错误返回 exit code `1`，stderr 打印干净错误信息，不输出 traceback。

---

## Output JSON Schema

```json
{
  "schema_version": 1,
  "tool": "segment_asr_routing_sandbox",
  "input_files": ["..."],
  "baseline_settings": {
    "confidence_threshold": 0.70,
    "min_segments": 1
  },
  "runs": [
    {
      "settings": {
        "confidence_threshold": 0.70,
        "min_segments": 1
      },
      "summary": {
        "total_windows": 5,
        "keep_auto": 1,
        "prefer_forced_fr": 1,
        "prefer_forced_en": 1,
        "needs_review": 1,
        "skip_window": 1
      },
      "changed_from_baseline": 0
    }
  ],
  "notes": [
    "This sandbox does not prove transcript correctness.",
    "This sandbox does not change production routing."
  ]
}
```

- `baseline_settings` 是第一个 setting combination（confidence threshold 列表第一个 × min segments 列表第一个）。
- `changed_from_baseline` 统计与 baseline 分类不同的 window 数量。
- 不声称任何参数设置是"最佳"，只报告 routing 建议如何变化。

---

## What Was Intentionally Not Changed

- `transcribe_to_srt()` 未改动。
- Production ASR 行为未改动。
- Pipeline 行为未改动。
- Web job 行为未改动。
- 字幕生成逻辑未改动。
- Provider / Language Profile 行为未改动。
- Release builder 未改动。
- M7.1 prototype 行为未改动。
- M7.2 analyzer 分类语义未改动。
- M7.3 golden fixture 预期分类未改动。
- 没有创建或恢复任何 M4 文件。

---

## Future Direction

- **M7.5** 可能扩展 sandbox 支持更多参数维度，例如 preview 长度阈值、forced-language 冲突权重。
- **M8** 可能将 sandbox 中验证的参数设置迁移到 production routing decision 的 configuration surface，但仍保持与 `transcribe_to_srt()` 的解耦。
- 真正的 ASS 字幕格式支持仍是独立 future work，与 M7.x 证据链无关。

---

## Limitations

> This sandbox does not prove transcript correctness and does not change production routing. It only replays M7.2 analyzer decisions over fixed evidence inputs.
