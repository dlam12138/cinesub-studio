# 轮次记录：模型参数与调优参考

- 时间：2026-07-23
- 用户目标：新增 `docs/model_tuning_reference.md`，整理 faster-whisper/CTranslate2、Qwen3-ASR/Forced Aligner 和 DeepSeek/OpenAI-compatible 翻译参数；只做官方资料归纳与项目配置索引，不修改运行逻辑。

## 已知事实与证据

- 本轮开始前已递归读取 `agents/thinking/` 全部文件，并读取用户粘贴的任务说明。
- 官方资料检索日期为 2026-07-23。
- 查阅的官方来源包括 SYSTRAN faster-whisper README 与 `transcribe.py`/`vad.py`、CTranslate2 文档、OpenAI Whisper 仓库、QwenLM/Qwen3-ASR README、DeepSeek Chat Completion/JSON Output/Reasoning Model/模型说明页面。
- 源码核对位置包括 `src/core/asr_runtime.py`、`src/core/transcribe.py`、`src/core/asr_retry.py`、`src/core/subtitle_resegment.py`、`src/config/language_profile_store.py`、`src/config/provider_store.py`、`src/pipeline/pipeline_config.py`、`src/pipeline/batch_worker.py`、`README.md`、`AGENTS.md` 和 `acceptance/v0_7_1_real_media_acceptance.md`。

## 决策摘要

- 新文档明确区分 `Official`、`Implemented`、`Verified`、`Experimental`、`Future` 和 `Rejected`。
- 对当前项目默认值只写源码可定位的结论；无法确认的 upstream 默认值写为 `Not confirmed` 或说明当前未使用。
- Qwen3-ASR、Qwen Forced Aligner 和 Demucs 均保持 future/reference 状态，不暗示已接入或已下载。
- DeepSeek 采样参数只作为生成控制说明；翻译质量拆分为上下文、术语、结构校验、局部复核、缓存和人工评审等项目级体系。

## 执行操作

- 新增 `docs/model_tuning_reference.md`。
- 在 `README.md` 源码开发区域新增“模型参数与调优参考”入口。
- 新增本 thinking 记录。
- 未修改模型参数、默认 preset、API、UI、版本号、发布文件名或运行逻辑。

## 验证结果

- `git diff --check` 通过。
- `git status --short --untracked-files=all` 在提交前只包含本任务预期改动：`README.md`、`docs/model_tuning_reference.md`、`agents/thinking/model_tuning_reference.md`。
- UTF-8 检查通过，无 replacement character。
- 双向 Unicode 控制字符扫描通过。
- 相对 Markdown 链接检查通过。
- 文档表格行基础扫描通过。
- 未发现绝对本地路径、API Key、私有媒体名称、完整 transcript 或虚构 benchmark。
- 仓库未发现现成 Markdown link checker 或文档测试入口；本轮使用上述本地检查替代。
- 已创建提交 `docs: add model tuning reference`。

## 未解决问题与下一步

- 本轮未修改运行时行为、模型参数、默认 preset、API、UI、版本号、Tag 或 Release。
- 后续如进入实际调参，应复用文档中的实验规则，先冻结 SHA、模型 snapshot、device、compute_type 和输入片段。
