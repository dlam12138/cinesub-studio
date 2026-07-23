# Model Tuning Reference

检索日期：2026-07-23。

本文只整理官方资料和 CineSub Studio 当前源码中的配置映射。它不是参数推荐广告，也不证明任何未经验证的识别率或翻译质量提升。

## 1. Purpose And Evidence Levels

| 标签 | 含义 |
| --- | --- |
| `Official` | 官方文档、官方仓库源码或模型卡明确说明 |
| `Implemented` | CineSub Studio 当前代码已经使用 |
| `Verified` | 已通过本项目自动化或真实媒体测试 |
| `Experimental` | 可以实验，但没有足够项目证据 |
| `Future` | 后续路线，目前未接入 |
| `Rejected` | 已有证据不支持，或风险大于收益 |

所有工程建议都必须标注证据等级。没有人工金标时，不得宣称 WER/CER 提升。

## 2. Current CineSub Studio Model Stack

**Evidence:** Implemented, Verified

- 正式便携版本仍为 `0.6.2`；`main` 包含未正式发布的 v0.7.x 源码候选。证据：`README.md`、`VERSION`、`tests/test_electron_shell_readiness.py`。
- 正式 ASR 后端仍是 faster-whisper。三种 ASR 模式是 `auto`、`fixed`、`multilingual`。证据：`src/core/asr_runtime.py`、`src/core/transcribe.py`。
- v0.7 ASR 质量闭环只允许固定配方 `local-retry-selective-v2`，不向用户暴露 candidate registry 或 candidate ID。证据：`AGENTS.md`、`src/core/asr_runtime.py`、`src/core/asr_retry.py`。
- Qwen3-ASR、Qwen Forced Aligner、Demucs 不属于当前产品链路。本文只作为 future reference。
- 翻译侧当前走 OpenAI-compatible 或 Anthropic 协议；内置 Provider 模板优先 DeepSeek。Provider 只保存 API Base、API Key 和模型，不保存 ASR 参数。证据：`src/config/provider_store.py`。

## 3. Configuration Precedence

**Evidence:** Implemented

当前有效配置优先级是：

1. 显式 CLI/Web 参数；
2. `quality_preset` 展开值；
3. Language Profile；
4. Provider；
5. 内置默认值。

源码位置：`src/pipeline/pipeline_config.py`、`src/core/transcribe.py`、`src/config/language_profile_store.py`、`src/config/provider_store.py`。

## 4. faster-whisper Parameter Reference

官方来源：[`SYSTRAN/faster-whisper` README](https://github.com/SYSTRAN/faster-whisper)、[`faster_whisper/transcribe.py`](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)、[`faster_whisper/vad.py`](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py)、[CTranslate2 documentation](https://opennmt.net/CTranslate2/)、[OpenAI Whisper repository](https://github.com/openai/whisper)。

| Parameter | Component | Official meaning | Current project usage | Current default | Preset mapping | Main effect | Potential benefit | Main risks | Evidence | Validation method | Official source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `model_size_or_path` | faster-whisper | Model name or local path used to load a Whisper model | `--model`; Web/Pipeline model; loaded by `WhisperModel` | `small` unless profile/preset overrides | `quality` maps model to `large-v3`; `speed`/`balanced` keep project/profile default | Text, performance, memory | Larger model can be tested on difficult speech | Slower, more VRAM, not guaranteed better on all media | Official, Implemented, Experimental | Same frozen clips, same device, compare cue-level human review | faster-whisper README |
| `language` | faster-whisper / Whisper | Source language code; `None` lets the model detect language | `auto` and `multilingual` pass `None`; `fixed` requires language | `None` in auto | Not changed by preset | Text, language detection | Avoid wrong auto detection when source is known | Wrong fixed language can damage output | Official, Implemented, Verified | A/B auto vs fixed on known-language clips | faster-whisper `transcribe.py` |
| `task` | Whisper | `transcribe` or `translate` task | Profiles store `task: transcribe`; product transcribes, LLM translates | `transcribe` | Not mapped | Text language | Keeps source subtitle available | Whisper translate would bypass project translation controls | Official, Implemented | Confirm output language and downstream translation invariants | faster-whisper `transcribe.py` |
| `beam_size` | Whisper decode | Beam search width | CLI/Web/Profile pass `beam_size` | `5` | Not mapped | Text, speed | Can test alternate decoding on hard audio | Larger beam is slower and not always more accurate | Official, Implemented, Experimental | One-variable ASR campaign | faster-whisper `transcribe.py` |
| `best_of` | Whisper decode | Number of candidates for sampling fallback | Not exposed | Not confirmed | Not mapped | Text, speed | Experimental only for sampling regimes | More cost, uncertain benefit | Official, Experimental | Only with fixed temperature experiment | faster-whisper `transcribe.py` |
| `patience` | Whisper decode | Optional patience factor for beam search | Not exposed | Not confirmed | Not mapped | Text, speed | May alter beam search termination | Slower, unclear subtitle benefit | Official, Experimental | Isolated decode A/B | faster-whisper `transcribe.py` |
| `length_penalty` | Whisper decode | Length penalty during beam search | Not exposed | Not confirmed | Not mapped | Text length | Could test truncation/repetition behavior | Biases length rather than correctness | Official, Experimental | Compare cue text length and omissions | faster-whisper `transcribe.py` |
| `temperature` | Whisper decode | Sampling temperature or fallback schedule | Not exposed for ASR | Not confirmed | Not mapped | Text, stability | Can test hallucination fallback behavior | Randomness, reproducibility loss | Official, Experimental | Cold-process repeated runs and human review | faster-whisper `transcribe.py` |
| `compression_ratio_threshold` | Whisper decode | Threshold used to treat overly compressed text as failed/hallucinated | Read from segment diagnostics; not user exposed | faster-whisper default, not confirmed in project | Not mapped | Stability, hallucination detection | Flags repetitive text | Too strict can reject valid dense speech | Official, Implemented, Experimental | Review `asr_review.json` suspicious cues | faster-whisper `transcribe.py` |
| `log_prob_threshold` | Whisper decode | Low average log probability threshold | Read from segment diagnostics; not user exposed | faster-whisper default, not confirmed in project | Not mapped | Stability | Flags low-confidence cues | Language/domain bias can over-warn | Official, Implemented, Experimental | Compare warnings with human review | faster-whisper `transcribe.py` |
| `no_speech_threshold` | Whisper decode | Probability threshold for no-speech decisions | Read from segment diagnostics; not user exposed | faster-whisper default, not confirmed in project | Not mapped | Silence handling | Helps find speech/silence mismatch | May miss quiet speech | Official, Implemented, Experimental | VAD uncovered plus human listening | faster-whisper `transcribe.py` |
| `condition_on_previous_text` | Whisper decode | Condition current decode on previous output | `AsrDecodeOptions`; default true; retry forces false | `True`; retry recipe false | Not mapped | Text continuity, repetition | Context can improve continuity | Can propagate hallucinations/repetition | Official, Implemented, Experimental | Compare duplicate rate and continuity | faster-whisper `transcribe.py` |
| `prompt_reset_on_temperature` | Whisper decode | Reset prompt when temperature fallback exceeds threshold | Not exposed | Not confirmed | Not mapped | Text, hallucination control | Future experiment for long hallucinations | Hard to attribute effects | Official, Experimental | One-variable long-form campaign | faster-whisper `transcribe.py` |
| `initial_prompt` | Whisper decode | Optional prompt prepended to decoding context | Built from `asr_hotword_prompt` and bounded glossary terms | Empty | Not mapped | Text, proper names | May bias names/terms | Overprompting can hallucinate or leak terms | Official, Implemented, Experimental | Name-hit review on frozen clips | faster-whisper `transcribe.py` |
| `hotwords` | faster-whisper | Hotword terms for ASR biasing where supported | Not directly exposed; project uses bounded prompt instead | Not used | Not mapped | Text, proper names | Future short-term experiment | No guarantee of stable improvement | Official, Experimental | Hotword A/B with human target list | faster-whisper `transcribe.py` |
| `repetition_penalty` | faster-whisper / CTranslate2 | Penalty discouraging repeated tokens | `AsrDecodeOptions`; retry uses `1.05` | `1.0`; retry `1.05` | Not mapped | Text, repetition | May reduce repeated loops | Can suppress legitimate repetition | Official, Implemented, Experimental | Duplicate cue and human review metrics | faster-whisper `transcribe.py` |
| `no_repeat_ngram_size` | faster-whisper / CTranslate2 | Blocks repeated n-grams of configured size | `AsrDecodeOptions`; retry uses `3` | `0`; retry `3` | Not mapped | Text, repetition | May reduce repeated fragments | Can damage repeated dialogue | Official, Implemented, Experimental | Cue-level regression review | faster-whisper `transcribe.py` |
| `suppress_blank` | Whisper decode | Suppress blank token at sampling start | Not exposed | Not confirmed | Not mapped | Text | Rare low-level decode experiment | Can affect timestamps/text starts | Official, Experimental | Token-level A/B only | faster-whisper `transcribe.py` |
| `suppress_tokens` | Whisper decode | Suppress selected token IDs | Not exposed | Not confirmed | Not mapped | Text | Could suppress known artifacts | High risk of deleting valid language tokens | Official, Experimental | Avoid unless exact artifact is proven | faster-whisper `transcribe.py` |
| `without_timestamps` | Whisper decode | Disable timestamp token prediction | Not exposed as user option | faster-whisper default behavior, not confirmed | Not mapped | Time axis | Usually not desirable for SRT generation | Can remove needed timing data | Official, Experimental | SRT timing validation | faster-whisper `transcribe.py` |
| `max_initial_timestamp` | Whisper decode | Limits first predicted timestamp | Not exposed | Not confirmed | Not mapped | Time axis | May constrain start drift | Can clip legitimate late speech starts | Official, Experimental | Compare first cue alignment | faster-whisper `transcribe.py` |
| `word_timestamps` | faster-whisper | Returns word-level timing metadata | CLI/Web/Pipeline quality loop | `False` unless preset/profile/explicit enables | `speed`: off; `balanced`: on; `quality`: on | Word timing, resegmentation | Enables deterministic subtitle resegmentation | More processing; word timings can contain edge cases | Official, Implemented, Verified | Existing word-timing and resegment tests plus real samples | faster-whisper README |
| `prepend_punctuations` | Word timing | Punctuation attached to following word during alignment | Not exposed | Not confirmed | Not mapped | Word timing | Future punctuation alignment tuning | Can affect cue splitting | Official, Experimental | Compare resegment text conservation | faster-whisper `transcribe.py` |
| `append_punctuations` | Word timing | Punctuation attached to previous word during alignment | Not exposed | Not confirmed | Not mapped | Word timing | Future punctuation alignment tuning | Can affect cue splitting | Official, Experimental | Compare resegment text conservation | faster-whisper `transcribe.py` |
| `hallucination_silence_threshold` | Word timing / hallucination control | Silence threshold used for hallucination handling | Not exposed | Not confirmed | Not mapped | Silence, hallucination | Future long-silence experiment | Incorrect trimming around quiet speech | Official, Experimental | Silent-region review campaign | faster-whisper `transcribe.py` |
| `vad_filter` | faster-whisper / Silero VAD | Filters silence before transcription | CLI/Web/Profile; disabled inside planned multilingual chunks | `True` in profiles/Web; `--no-vad` disables | Not mapped | Segmentation, silence, performance | Reduces silent hallucinations and workload | Can remove quiet speech | Official, Implemented, Experimental | VAD uncovered windows plus listening | faster-whisper README, `vad.py` |
| `vad_parameters` | Silero VAD | VAD thresholds and durations | `threshold`, `min_silence_duration_ms`, `speech_pad_ms` from `AsrDecodeOptions`; retry recipe changes threshold/silence | threshold `0.5`, silence `2000ms`, pad `400ms`; retry threshold `0.4`, silence `500ms` | Not mapped | Segmentation, silence | Can recover short pauses or reduce hallucination | Over/under segmentation | Official, Implemented, Experimental | Existing 28-run ASR campaign contract | faster-whisper `vad.py` |
| `chunk_length` | faster-whisper batched/inference | Chunk length for batched pipelines | Product uses custom multilingual VAD block planning, not public faster-whisper batch pipeline | Not used | Not mapped | Performance, segmentation | Future batch pipeline tuning | Different chunking can change text/timing | Official, Future | Isolated batch-pipeline prototype | faster-whisper README |
| `batch_size` | faster-whisper batched/inference | Inference batch size | Not used for ASR; translation uses separate batch size | Not used for ASR | Not mapped | Performance, memory | Can improve throughput in batch mode | OOM and changed chunk behavior | Official, Future | GPU memory/time profiling | faster-whisper README |
| `device` | CTranslate2 | Compute device such as CPU or CUDA | CLI/Web/Profile; `auto` prefers CUDA and can fall back CPU; explicit CUDA fails fast if invalid | CLI default `auto`; built-in profiles often CPU | Not mapped | Performance, memory | GPU speed | CUDA compatibility failures | Official, Implemented, Verified | Runtime diagnostics and model preflight | CTranslate2 docs |
| `compute_type` | CTranslate2 | Weight/activation compute precision | CLI/Web/Profile; default chosen from device | CPU usually `int8`; CUDA usually `float16` when unspecified | Not mapped | Performance, memory, numeric behavior | Lower memory or faster inference | Compatibility or accuracy changes | Official, Implemented, Experimental | Same clip, same device, compare output/time/VRAM | CTranslate2 docs |
| `local_files_only` | faster-whisper / Hugging Face | Load from local files only; avoid downloads | CLI hidden-on Web default and release tests require local model availability | Web/Pipeline true by policy | Not mapped | Reproducibility, offline safety | Prevents silent downloads | Fails when model absent | Official, Implemented, Verified | Model locator preflight and offline tests | faster-whisper README |

## 5. VAD And Audio Segmentation

**Evidence:** Official, Implemented, Experimental

`vad_filter` and `vad_parameters` are official faster-whisper/Silero controls. CineSub Studio also has project-level multilingual planning: one audio extraction, VAD speech spans, 45s target blocks, 60s max blocks, 8s long silence split, and 0.8s overlap (`src/core/asr_runtime.py`). No effective speech block is a hard failure.

Problem-oriented guidance:

| 问题 | 优先检查 | 不建议立即做 | Evidence |
| --- | --- | --- | --- |
| 静音区生成文字 | VAD threshold/silence, no-speech diagnostics, hallucination controls | 只提高模型大小 | Official, Implemented, Experimental |
| 长片漂移 | 分段、上下文继承、VAD uncovered、真实 campaign | 单纯增加上下文 | Implemented, Experimental |
| OOM | model size、batch、compute type、device | 静默 CPU fallback | Official, Implemented |

## 6. Word Timing And Subtitle Resegmentation

**Evidence:** Official, Implemented, Verified

`word_timestamps` enables word timing. CineSub Studio uses `SubtitleResegmenter` to deterministically rebuild cue boundaries when resegmentation is enabled. Existing tests verify word timing data flow, text conservation, time-axis conservation, deterministic splitting, and the French zero-duration token fix.

Important boundary: resegmentation can improve subtitle readability and cue shape, but it does not prove the recognized words are more correct.

## 7. ASR Prompting And Proper Names

**Evidence:** Official, Implemented, Experimental

The project uses bounded `initial_prompt` text from `asr_hotword_prompt` and relevant glossary terms. It does not expose an unbounded glossary injection mechanism. Hotword/name experiments must use a fixed term list and human-reviewed target names.

| 问题 | 优先检查 | 不建议立即做 |
| --- | --- | --- |
| 语言识别错误 | 固定 `language` when known | 盲目增大 `beam_size` |
| 专名错误 | 短 prompt/hotwords and project glossary | 注入完整 glossary |
| 双语切换 | `multilingual` route and true bilingual samples | 对所有电影默认 multilingual |

## 8. Decode Failure And Hallucination Controls

**Evidence:** Official, Implemented, Experimental

The project records `avg_logprob`, `compression_ratio`, and `no_speech_prob` in ASR review reports and marks suspicious cues. These signals guide review and controlled retry planning, but default presets do not automatically replace text.

Rejected claims:

- `beam_size` 越大越准确。
- `large-v3` 一定比 `small` 更适合所有素材。
- 开启 prompt/hotwords 一定提高专名识别。
- `quality` preset 已证明提高文本准确率。

## 9. Device, Compute Type And Performance

**Evidence:** Official, Implemented, Verified

CineSub Studio resolves device through runtime diagnostics and model preflight. `device=auto` prefers CUDA when compatible and otherwise can fall back to CPU; explicit `cuda` should fail fast with diagnostic details. `local_files_only=True` is required for release/offline safety and tests must not trigger model downloads.

## 10. Current Quality Presets

**Evidence:** Implemented, Verified

Source: `src/core/asr_runtime.py`.

| Setting | speed | balanced | quality |
| --- | ---: | ---: | ---: |
| Model | project/profile/default | project/profile/default | `large-v3` unless explicit model overrides |
| Word timestamps | off | on | on |
| Resegment | off | on | on |
| Retry | off | dry-run | dry-run |
| Automatic text replacement | no | no | no |

Preset notes:

- A preset is a configuration bundle, not a model.
- `quality` does not mean “proven more accurate text.”
- `balanced` and `quality` may improve subtitle splitting through word timing/resegmentation, not necessarily word correctness.
- Explicit single parameters still override preset-expanded values.

## 11. Qwen3-ASR Future Reference

Official sources: [`QwenLM/Qwen3-ASR` README](https://github.com/QwenLM/Qwen3-ASR), Qwen model cards on Hugging Face, and official inference examples.

**Evidence:** Official, Future

| Parameter | Component | Official meaning | Current project usage | Current default | Preset mapping | Main effect | Potential benefit | Main risks | Evidence | Validation method | Official source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `language` | Qwen3-ASR | Source language hint/selection in official inference | Not integrated | Not applicable | Not mapped | Text | Future ASR comparison | Wrong language hint risk | Official, Future | Separate ASR campaign | Qwen3-ASR README |
| `context` | Qwen3-ASR | Optional contextual prompt/text for recognition | Not integrated | Not applicable | Not mapped | Text, names | Proper-name context experiment | Prompt over-bias | Official, Future | Fixed-context A/B | Qwen3-ASR README |
| `max_new_tokens` | Transformers | Generation output token cap | Not integrated | Not applicable | Not mapped | Truncation, performance | Prevent runaway output | Can truncate transcript | Official, Future | Long-clip truncation audit | Qwen3-ASR examples |
| `max_inference_batch_size` | Qwen3-ASR | Batch size limit in official examples/backend | Not integrated | Not applicable | Not mapped | Throughput, memory | Batch throughput | OOM or latency spikes | Official, Future | GPU memory profiling | Qwen3-ASR README |
| `dtype` | Transformers | Model precision such as fp16/bfloat16 | Not integrated | Not applicable | Not mapped | Memory, speed | Fit model locally | Compatibility issues | Official, Future | Load preflight plus A/B | Transformers docs / Qwen examples |
| `device_map` | Transformers | Model device placement | Not integrated | Not applicable | Not mapped | Memory, speed | Multi-device placement | Windows portability complexity | Official, Future | Portable diagnostics | Transformers docs / Qwen examples |
| Transformers backend | Qwen3-ASR | Standard Hugging Face inference route | Not integrated | Not applicable | Not mapped | Runtime | Better Windows portability than vLLM | Large dependency footprint | Official, Future | Isolated prototype | Qwen3-ASR README |
| vLLM backend | Qwen3-ASR | High-throughput server/inference route | Not integrated | Not applicable | Not mapped | Throughput | Server throughput | Not current Windows portable default | Official, Future | Non-product benchmark only | Qwen3-ASR README |
| local model directory | Qwen3-ASR | Load separately downloaded model files | Not integrated | Not applicable | Not mapped | Offline safety | Product-like offline mode | Large downloads and version drift | Official, Future | Hash/revision preflight | Qwen model cards |
| model download/offline loading | Qwen3-ASR | Official model acquisition and local loading | Not integrated | Not applicable | Not mapped | Reproducibility | Future offline support | Must not imply bundled model | Official, Future | Explicit download plan and hashes | Qwen model cards |
| streaming recognition | Qwen3-ASR | Streaming/long audio capability where documented | Not integrated | Not applicable | Not mapped | Latency, long audio | Future real-time route | Different segmentation semantics | Official, Future | Separate product design | Qwen3-ASR README |
| fine-tuning entry | Qwen3-ASR | Official training/fine-tuning path if provided | Not integrated | Not applicable | Not mapped | Domain adaptation | Future research | Data, cost, legal boundary | Official, Future | Private dataset governance first | Qwen docs |

Qwen3-ASR is not in the formal product path. Official benchmarks must not be treated as proof on CineSub Studio movie material. The model must be downloaded separately if ever tested.

## 12. Qwen Forced Aligner Future Reference

**Evidence:** Official, Future

| Parameter | Component | Official meaning | Current project usage | Current default | Preset mapping | Main effect | Potential benefit | Main risks | Evidence | Validation method | Official source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `forced_aligner` | Qwen / alignment | Aligns known text to audio timestamps | Not integrated | Not applicable | Not mapped | Time axis | Better timestamps for trusted text | Does not fix wrong ASR words | Official, Future | Text-fixed alignment test | Qwen docs |
| `return_time_stamps` | Qwen / alignment | Requests timestamp output when supported | Not integrated | Not applicable | Not mapped | Time axis | Timestamp extraction | Timestamp quality unknown on films | Official, Future | Compare with human timing review | Qwen docs |
| long audio segmentation limits | Qwen / alignment | Official limits for long audio chunks | Not integrated | Not applicable | Not mapped | Segmentation | Future long-form planning | Chunk boundary drift | Official, Future | Same 180-300s clips plus long clips | Qwen docs |

Forced alignment solves text-to-time alignment. It does not repair incorrect transcript text and must not be marketed as ASR correction.

## 13. DeepSeek Translation Parameter Reference

Official sources: [DeepSeek Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion), [DeepSeek JSON Output guide](https://api-docs.deepseek.com/guides/json_mode), [DeepSeek Reasoning Model guide](https://api-docs.deepseek.com/guides/reasoning_model), [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing).

| Parameter | Component | Official meaning | Current project usage | Current default | Preset mapping | Main effect | Potential benefit | Main risks | Evidence | Validation method | Official source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `temperature` | DeepSeek/OpenAI-compatible | Sampling randomness | CLI/Web translation temperature; repair/judge stages often use `0.0` | Initial translation `0.2` | Not ASR preset mapped | Style, variability | Conservative first pass | Higher values can reduce consistency | Official, Implemented, Experimental | Fixed subtitle sample and human review | DeepSeek API |
| `top_p` | DeepSeek/OpenAI-compatible | Nucleus sampling | Not sent by current code | Not used | Not mapped | Style, variability | Future sampling experiment | Do not vary heavily with temperature | Official, Experimental | One variable at a time | DeepSeek API |
| `max_tokens` | DeepSeek/OpenAI-compatible | Max generated tokens | OpenAI-compatible call uses `4096`; provider probe uses `256` | `4096` translation request | Not mapped | Truncation, cost | Prevent runaway output | Can truncate batches | Official, Implemented | Check `finish_reason`, missing IDs, batch split | DeepSeek API |
| `stop` | DeepSeek/OpenAI-compatible | Stop generation on sequences | Not sent | Not used | Not mapped | Output boundaries | Possible future guard | Can cut valid JSON | Official, Experimental | JSON validity tests | DeepSeek API |
| `response_format` | DeepSeek JSON Output | Requests JSON object output where supported | Not currently sent; project validates JSON text itself | Not used | Not mapped | Structure | Future stricter JSON mode | Still requires validation | Official, Experimental | Schema/id/field validation | DeepSeek JSON Output |
| JSON Output | DeepSeek | Provider-supported JSON output mode | Project prompts strict JSON and parses/repairs common wrappers | Prompt-based, not API-enforced | Not mapped | Structure | Reduces parsing failures if adopted | Invalid or incomplete JSON still possible | Official, Implemented, Experimental | Existing structured output tests | DeepSeek JSON Output |
| `finish_reason` | DeepSeek/OpenAI-compatible | Completion ending reason such as stop/length | Current parser does not enforce it | Not checked | Not mapped | Truncation detection | Future stricter reliability | Ignoring it can miss cutoffs | Official, Experimental | Add tests for length/content_filter | DeepSeek API |
| thinking / non-thinking mode | DeepSeek models | Reasoning models may expose thinking/non-thinking behavior depending on model/API | Provider template separates Flash/Pro-like roles; no product guarantee | Template defaults in `provider_store.py` | Not mapped | Cost, latency, reasoning | Use Pro for difficult review stages | Expensive, may not improve subtitles | Official, Implemented, Experimental | Fixed translation benchmark | DeepSeek guides |
| system/user messages | Chat Completions | System sets behavior, user carries task data | Project builds system prompt and user batch payloads | Implemented | Not mapped | Instruction control | Stable constraints | Prompt conflicts and overlong context | Official, Implemented | Prompt regression tests | DeepSeek API |
| batch item count | Project/OpenAI-compatible | Project-side batch size, not a DeepSeek model parameter | `translation_batch_size` | `20` | Not mapped | Cost, truncation, context | Throughput | Missing IDs, truncation, cross-item bleed | Implemented, Experimental | Batch split tests and human review | Project code |
| retry and rate limits | Provider/API | 429/5xx handling and retry-after behavior | HTTP 429/5xx bounded retries; preview repair budget | Three attempts for selected errors | Not mapped | Reliability, cost | More robust transient handling | Repeated cost, rate pressure | Implemented | Mock HTTP and real provider smoke | DeepSeek API + project code |
| context length | Provider/model | Model-specific input/output limit | Project detects context-too-long and can split | Provider-dependent | Not mapped | Truncation | Smaller batches/windows | Hidden cutoff if not checked | Official, Implemented | 413/400 tests and split audit | DeepSeek API |
| model name / API Base | DeepSeek Provider | Provider endpoint and model ID | Provider template defaults to DeepSeek OpenAI-compatible base and translation models | Template default from `provider_store.py` | Not mapped | Model selection | Separate fast/quality roles | Model availability changes | Official, Implemented | Provider connection test | DeepSeek API |
| deterministic/random tradeoff | LLM sampling | Lower randomness is more reproducible; higher randomness is more variable | Initial `0.2`; judge/repair often `0.0` | Conservative | Not mapped | Consistency | Stable JSON and terms | Flat style or unstable creative edits | Official, Implemented, Experimental | Blind review and edit-count metrics | DeepSeek API |

## 14. Translation Quality Is Not Only Sampling

**Evidence:** Implemented, Experimental

模型采样参数不等于完整翻译质量体系。

Translation quality must be evaluated across:

1. 模型和采样；
2. 上下文；
3. 术语；
4. 批量边界；
5. JSON 结构；
6. 漏译和错译检测；
7. 局部复核；
8. 人工锁定和缓存；
9. 字幕长度与可读性；
10. Provider 可靠性。

Conservative guidance:

- 初译通常使用较低 `temperature`。
- 文艺润色应作为独立实验，不应默认重译整部电影。
- 不建议同时大幅调整 `temperature` 与 `top_p`。
- JSON Output 仍必须校验条目数量、ID、字段和 `finish_reason`。
- LLM 不得修改 SRT 时间戳，也不得自由增删字幕条目。

## 15. Experiment Rules

**Evidence:** Implemented, Verified

ASR/translation tuning must follow this contract:

```text
一次只改变一个主要变量；
使用相同输入片段；
固定模型 snapshot；
固定 device 和 compute_type；
固定 evaluated SHA；
使用独立冷进程；
禁止模型下载；
记录有效配置；
保留输出哈希；
记录耗时和显存；
人工审核变化 cue；
没有人工金标时不得宣称 WER/CER 提升；
OCR 只能作为弱证据；
失败结果不得删除。
```

ASR should reuse the existing real-media campaign contract. Translation should add fixed subtitle samples, name consistency, omission rate, number preservation, item conservation, source-language residue, manual edit count, model calls, and token cost.

## 16. Verified, Unverified And Rejected Claims

### Verified

**Evidence:** Verified

- 旧 CLI 与 `speed` 输出兼容性。
- word timing 数据链路。
- 确定性字幕重切分。
- 时间轴与文本守恒。
- 法语零时长 token 修复。
- `quality=dry_run` 不自动替换字幕。
- Pipeline 预检和配置一致性属于工程可靠性，而不是模型效果提升。

### Unverified

**Evidence:** Experimental

- `local-retry-selective-v2` 自动替换能够提高 ASR 文本质量。
- hotwords 能稳定提升专名。
- Qwen3-ASR 在本项目电影素材中优于 `large-v3`。
- Demucs 能稳定提高影视对白识别。
- 当前 ASR 参数组合能提高正式 WER/CER。
- 任何翻译参数组合能稳定提升人物语气和剧情理解。

### Rejected Claims

**Evidence:** Rejected

- `large-v3` 一定比 `small` 更适合所有素材。
- `beam_size` 越大越准确。
- 开启 hotwords 一定提高专名识别。
- Forced Aligner 可以修正 ASR 错字。
- `quality` preset 已证明提高文本准确率。
- OCR 可以替代人工金标。
- 更高 `temperature` 会让翻译更高级。
- Qwen 官方 benchmark 证明它在本项目上更好。

## 17. Official Sources

- faster-whisper README: <https://github.com/SYSTRAN/faster-whisper>
- faster-whisper `transcribe.py`: <https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py>
- faster-whisper `vad.py`: <https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py>
- CTranslate2 documentation: <https://opennmt.net/CTranslate2/>
- OpenAI Whisper repository: <https://github.com/openai/whisper>
- Qwen3-ASR official repository: <https://github.com/QwenLM/Qwen3-ASR>
- DeepSeek Chat Completion API: <https://api-docs.deepseek.com/api/create-chat-completion>
- DeepSeek JSON Output: <https://api-docs.deepseek.com/guides/json_mode>
- DeepSeek Reasoning Model: <https://api-docs.deepseek.com/guides/reasoning_model>
- DeepSeek models/pricing: <https://api-docs.deepseek.com/quick_start/pricing>

## Current Project Evidence Index

- `README.md`: formal version boundary, v0.7.x source candidate note, `quality=dry_run` summary.
- `AGENTS.md`: delivery boundary, ASR governance, configuration precedence.
- `src/core/asr_runtime.py`: ASR modes, decode options, quality presets, retry recipe version, VAD block planning.
- `src/core/transcribe.py`: faster-whisper session creation, local model loading, ASR calls, retry/resegment/report flow.
- `src/core/asr_retry.py`: retry planning, hard rejection and transaction validation.
- `src/core/subtitle_resegment.py`: deterministic word-timing resegmentation.
- `src/config/language_profile_store.py`: built-in ASR defaults and profile boundaries.
- `src/config/provider_store.py`: DeepSeek/OpenAI-compatible provider fields and connection probe.
- `src/pipeline/pipeline_config.py`: CLI/Profile/Provider/default resolution.
- `src/pipeline/batch_worker.py`: ASR signature fields and artifact-stage fingerprints.
- `acceptance/v0_7_1_real_media_acceptance.md`: anonymous real-media acceptance conclusions.
