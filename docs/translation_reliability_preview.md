# 翻译可靠性预览模式

状态：自动门槛 `pass`、人工盲评 `pending`，发布仍为 `no_go`，生产默认关闭。

## 能力边界

- `off` 保持原有请求数和失败行为，不执行自适应拆分或独立修复。
- `preview` 为每个原始批次保留现有结构化重试；仅对结构错误、漏 ID、上下文过长和重复中断递归二分。
- 401/403/404 快速失败；429/5xx 仅做有界退避，不通过拆分放大请求。
- 成功子批立即原子合并缓存。任一子批最终失败时，保留缓存证据，但不生成不完整成品。
- 最终 SRT 使用同目录临时文件与 `os.replace()`；失败时旧成品不变。

## 可自动修复项

共享纯检测规则仅允许修复：空译文、LLM 废话、源译规范化后明显相同、以及规则确认的明显未翻译文字。CPS、过长、外文专名、mixed-language、时间轴和其他 warning 不会自动改写。

Preview 会通过生产与验证工具共用的纯函数，把问题 cue 向前后各扩展一条，并合并相邻或重叠窗口。请求字段明确区分 `source_text`、`existing_translation`、目标语言和窗口外只读上下文；模型必须返回窗口内全部 ID，不能返回上下文 ID。修复请求固定使用 `temperature=0`，以降低评测与生产路径的随机差异。

质量链依次生成主模型候选、必要时执行带脱敏失败码的纠正、生成质量模型独立候选，再由质量模型判定器选择候选标签或拒绝全部。判定器不能返回或改写字幕正文。最终候选仍需通过阻断项、相邻重复、ID 和时间轴门槛，窗口才会整体接受；缓存先原子写入成功，内存字幕才会一起更新。预算耗尽或任一步失败时保留原窗口并标记 `review_required`。

## 公开接口

Language Profile 可保存：

```json
{
  "translation_reliability": {
    "mode": "off",
    "max_extra_requests": 12
  }
}
```

CLI、Web 写接口与 Pipeline 支持 `translation_reliability_mode=off|preview` 及 `translation_max_extra_requests=0..50`。优先级为 CLI/请求显式值 > Language Profile > `off/12`。Provider 不承载这两项设置。

Provider 可选保存 `translation_quality_model`；优先级为 CLI 显式质量模型 > Provider 质量模型 > 主翻译模型。旧 Provider 缺少该字段时保持兼容。

`translate_srt()` 返回脱敏 `TranslationRunSummary`，对外摘要只含缓存命中、请求数、拆分数、修复/未解决数、各候选与判定阶段请求数、窗口拒绝原因、候选阻断项计数和预算状态，不含正文、API Key、cue ID 或绝对路径。

摘要还包含修复窗口的尝试、接受、拒绝和相邻重复拒绝计数；不返回 cue ID。

## 验证报告与匿名评审

验证报告 schema v3 按共享窗口规划记录模型路由、候选与判定阶段、两轮窗口尝试、接受、拒绝、阻断项、相邻重复/包含计数、结构稳定性、请求数和预算状态。匿名 A/B 以整个窗口为随机化单位，同一窗口内的 cue 不会混用 baseline 与 Preview；窗口外两版保持 baseline 一致。答案表和评审表只在两轮自动门槛全部通过后生成。

自动门槛要求两轮 cue 数、ID 与时间轴不变，阻断项归零，相邻重复不恶化，请求数不超预算，且没有未解决或被拒绝的窗口。任一条件失败即维持 `no_go/off`。

## 发布准入

早期单 cue Preview 的自动门槛虽通过，但人工听审发现跨 cue 割裂和重复；随后确定性小窗口矩阵又因第二轮回显源文而自动 `no_go`。2026-07-17 的 schema v3 质量链使用 `deepseek-v4-flash` 生成、`deepseek-v4-pro` 生成质量候选并判定，在固定 24-cue 样本两轮均接受 2/2 窗口，阻断项归零、结构及相邻重复稳定，使用 `14/40` 次 HTTP，自动门槛通过并生成窗口级 A/B 与两段音频。

人工盲评尚未完成，因此发布结论仍为 `no_go`。Web 高级设置和 Profile 编辑器继续不展示开关，内置 Profile 与生产默认仍为 `off`。验收记录见 `acceptance/translation_reliability_preview.md`。
