# Milestone 2 Acceptance: 用户可读环境诊断

日期：2026-07-01

## 验收结果

- `ruff` passed
- `pytest` passed: 16 passed
- `scripts/smoke_test.ps1` passed
- Web smoke passed: home=200, diagnostics=200
- `scripts/dev_check.ps1` passed

## 当前诊断基线

- diagnostics status: `warning`
- warning 原因：当前 Python 为 3.13.3，`python_supported=false`
- FFmpeg: bundled / 项目内置
- CUDA: ready
- Web diagnostics API: ok
- Provider 诊断项已加入，并完成 JSON secret leak scan

## 稳定 API 字段

`GET /api/runtime/diagnostics` 的用户可读诊断结构包含以下稳定字段：

- `ffmpeg_source`
- `diagnostic_summary`
- `diagnostic_items`
- `diagnostic_items[].status`
- `diagnostic_items[].blocking`

截图等人工 QA 资料放入 `acceptance/screenshots/`，不提交到 Git。
