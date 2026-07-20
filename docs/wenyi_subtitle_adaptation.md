# WenYi 字幕适配说明

实验策略 `wenyi_review` 固定参考 WenYi `v0.3.2`，commit
`d07298e1139c631a5ddba0efc3c7a6956cf4b1af`。上游采用 MIT License。

适配范围包括宽容 JSON 解析、模型档位、全局分析、Prompt 上下文顺序、
滚动译文、相关术语、Reviewer 和一致性审计。运行时不依赖 WenYi，
也不包含 EPUB/PDF、CLI、Provider、Pydantic、SQLite、Polisher 或写回逻辑。

字幕适配器的硬边界：

- 输入 ID、顺序、时间轴和 source 是不可变记录；
- `fast` 使用 Flash/`llm_model`，`cheap` 与 `strong` 使用
  Pro/`translation_quality_model`；
- 未配置 Pro 时立即失败，禁止降级为 Flash；
- 修复必须经过匿名 Pro 裁决；
- 短译必须满足预算且六项等价字段全部为 `true`；
- 一致性只报告，不自动替换；
- 报告和接口只读，不提供字幕回写。

通过离线测试、6 片真实回归和 151 条盲审前，`wenyi_review` 不出现在
Web 策略选择器中，默认策略仍为 `standard`。
