# Stage 3A 法语质量 Campaign 执行与结论

## 用户目标

- 只执行 Stage 3A 法语电影质量 Campaign，不把英语、普通话或真实代码切换素材作为前置条件。
- 使用三个授权法语源媒体的六个互不重叠窗口，完成固定 28-run ASR Campaign。
- 准备本地 `small` 模型，保持 Campaign `local_files_only=True`。
- ASR 完成后冻结 source SRT，执行 `standard` 与 `three_pass` 翻译 T1。
- 生成匿名公开证据；本轮不创建 PR、合并、Tag 或 Release。

## 已知事实与证据

- 分支为 `codex/stage3-asr-translation-quality-campaign`。
- Stage 3A scope 工具提交为 `9d1cbf1`；Campaign 前 dry-run/解码波动门禁修正提交为 `e0be73e`。
- 三个授权源媒体各拆分两个 120 秒非重叠窗口，共六个样本、720 秒。
- 六个 reference 均为 `ocr_weak`；`gold_verbatim=0`、`production_subtitle=0`。
- `small` 与 `large-v3` 都通过 CUDA/float16/local-only 真实模型加载预检。
- 新鲜 28-run 全部成功，ASR evaluated SHA 为
  `e0be73e73c681c7f0eefa4ba19ad5ecdc080643b`。
- 7 组 quality/control 配对的输入、解码配置、运行时配置和允许差异均通过；
  quality 的 retry accepted window 全部为 0。
- 1/7 独立配对出现输出哈希波动；同 profile 重复运行也可复现波动，判定为
  CUDA 独立解码可复现性风险，而不是 dry-run 应用 retry。
- ASR evaluator 在没有单独 `asr_review.json` 时原先失败；已改为回退使用 run
  report 内嵌 retry 审计，提交 `708eed7`。
- 翻译 T1 已生成六个 `standard` 和六个 `three_pass` translated-only SRT。
  12 个候选均保持 cue ID 与时间轴，无空 cue 或可疑包装。
- 私有 deterministic blind review 与 answer key 已生成，但没有人工填写，也没有
  授权中文参考或具备法语能力的双语审核。

## 本轮决策摘要

- ASR text accuracy：`inconclusive`，不得对 weak OCR 计算或宣称正式 WER/CER。
- ASR model decision：`inconclusive`；模型体积和结构计数不能替代文本准确率证据。
- ASR resegment decision：`inconclusive`；长行数下降，但高 CPS 和 gap 上升，且
  没有完成可读性盲审。
- ASR retry decision：`keep_dry_run`；没有 planned/executed/accepted 候选窗口，
  不允许默认 `apply`。
- Translation strategy decision：`inconclusive`；结构门禁通过不代表 fidelity 或
  fluency 晋级。
- Stage 3A 结论为 `Fail`，原因是 gold 与合格人工审核不足；Stage 3B 可以继续，
  Release Prep 不允许。
- 为满足用户明确指定的匿名 Stage 3A 文件，AGENTS 与 `.gitignore` 只增加四个
  精确 allowlist 路径，未放宽其他 acceptance 私有内容，也未使用 `git add -f`。

## 实际执行的操作

- 推送既有工具提交和 scope 提交。
- 使用仓库已有下载工具准备本地 `small` snapshot，并完成双模型预检。
- 生成私有授权 manifest、六个媒体窗口和 SHA 绑定合同。
- 执行一次旧门禁 Campaign，发现独立 CUDA 解码非确定性；增加回归测试并修正
  acceptance 判定，再以新 SHA 全量重跑 28 次。
- 运行 ASR 离线 evaluator，生成 sample/source-group 汇总和私有盲审材料。
- 修复 evaluator 的内嵌 retry report 回退并完成全量测试。
- 冻结六个 quality SRT，使用同一 Provider 配置完成 12 个翻译 T1 候选。
- 生成私有 T1 结构报告、manifest、盲审表和 answer key。
- 新增匿名公开 Markdown、summary JSON、ASR CSV 和 translation CSV。

## 验证结果

- 定向 acceptance/evaluator 测试通过。
- 两次全量 pytest 均通过，共 538 个测试。
- Ruff 与 `git diff --check` 通过。
- 28/28 ASR run 成功；合同状态 `pass_with_decode_variance`。
- ASR evaluator：28 runs、6 samples、3 source groups、gold sample count 0。
- 翻译：12/12 非空候选，结构门禁通过。
- 公开 JSON 通过 `assert_public_safe`；两个 CSV 可解析；匿名文件隐私关键字扫描
  未发现绝对路径、API key、媒体路径、候选正文或翻译正文。

## 未解决问题与下一步

- 需要授权、人工核对的法语逐字稿，才能形成正式 WER/CER 与模型准确率结论。
- 需要合格法语 reviewer 完成 ASR model/resegment 盲审。
- 需要授权中文参考或合格法中双语 fidelity 审核，并完成翻译盲审。
- CUDA 困难片段的独立解码非确定性需作为风险保留；本轮未修改生产解码默认。
- 完成以上人工证据后，才能重新判定 Stage 3A 与 Release Prep。
- 本轮不创建 PR、合并、Tag 或 Release。
