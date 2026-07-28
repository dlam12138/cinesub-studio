# v0.7.1 Stage 3A Public Gold Addendum

## Conclusion

**Public-gold ASR execution: Pass. Production promotion: No.**

The frozen 40-run campaign completed successfully on six French
`gold_verbatim` samples. It closes the missing formal WER/CER measurement gap,
but it does not replace the original private-film Stage 3A result. SUMM-RE is
meeting-style speech rather than film dialogue. Translation assisted review is
complete but inconclusive; human French-Chinese fidelity review is incomplete.

- ASR model decision: `keep_current_model_split`.
- ASR resegment decision: `reject`.
- ASR retry decision: `keep_dry_run`.
- Stage 3A Public Gold ASR: Pass.
- Translation assisted review: `complete`.
- Translation human fidelity review: `not_completed`.
- Allows production ASR change: No.
- Allows production translation change: No.
- Allows quality-improvement claim: No.
- Allows quality-neutral Release Prep: Yes.
- Allows Tag or GitHub Release: No.

The existing private-film campaign remains `execution pass / evidence gate
fail`; its evidence and conclusions are unchanged.

## Dataset and Frozen Scope

MediaSpeech FR was assessed first, but its downloaded public archive did not
provide a recoverable three-group source identity suitable for the matched
control protocol. The fallback therefore used only the SUMM-RE `dev` split at
revision `6b5492d1cea1e483131627c939f82c3989c52b0d`; no train data was used.

- Primary archive assessed: OpenSLR SLR108 MediaSpeech FR, CC BY 4.0,
  637,704,318 bytes, archive hash prefix `edefa83dab25`.
- Primary source: <https://www.openslr.org/108/>.
- Dataset: SUMM-RE, CC BY-SA 4.0.
- Citation: *SUMM-RE: A corpus of French meeting-style conversations* (2024).
- Source: <https://huggingface.co/datasets/linagora/SUMM-RE>
- Evaluated source SHA: `ec416ef18cf531e93a562a65894bc0ba5201ca15`.
- Dataset snapshot identity prefix: `229e865cb064`.
- Scope: 3 anonymous source groups, 2 speaker tracks per group, 6 samples.
- Reference type: 6 × `gold_verbatim`.
- Total duration: 295.994 seconds.
- Runtime: CUDA, float16, local files only.
- Campaign: 40 planned, 40 successful.
- Reference-alignment sanity: Pass; 6 clips across 3 source groups.

The public evidence contains no transcripts, translations, original source
identifiers, local paths, API material, prompts, or review answer key.

The private alignment audit independently traced the selected speaker rows,
rebuilt prepared audio from the local official dataset assets, verified the
gold segment projection and frozen candidate hashes, and confirmed complete
candidate window assignment. The audit does not publish audio or text.

## Matched ASR Model Evidence

The matched comparison is `small-quality-control` versus `large-control`.

| Metric | small | large-v3 |
| --- | ---: | ---: |
| Corpus WER | 0.95544041 | 0.93056995 |
| Corpus CER | 0.35322048 | 0.36705202 |
| Substitutions / insertions / deletions | 214 / 1 / 707 | 219 / 4 / 675 |
| Empty outputs | 1 | 4 |
| Hallucination-like insertions | 0 | 1 |
| End-to-end seconds | 29.145 | 66.660 |

The relative WER improvement is 2.603%. A deterministic 10,000-resample
clip-level bootstrap (seed `74fbee18d239684d`) estimates a mean WER delta of
0.02477109 with 95% interval `[0.00325380, 0.04629630]`. Large-v3 wins 20 of 52
clips, small wins 14, and 18 tie.

This is not sufficient for promotion. The improvement is below the frozen 5%
threshold, large-v3 is about 2.29× slower, it increases empty and
hallucination-like outputs, and one of six samples regresses by 5.298 WER
points. GPU memory telemetry was unavailable during this run and is not
reported as zero usage.

Decision: `keep_current_model_split`.

## Resegmentation and Retry

The matched resegmentation comparison had no zero-duration cues or overlaps,
but normalized text changed on all six samples. Structural improvements cannot
justify altering recognized text under this contract.

Decision: `reject`.

No suspicious retry window was planned, executed, or accepted in any matched
quality/control pair.

Decision: `keep_dry_run`.

## Translation T1 Handoff

The same six gold French SRT files were translated using one frozen Provider
configuration with only the strategy changed:

- 6 `standard` candidates;
- 6 `three_pass` candidates;
- 52 cues per strategy;
- all IDs and timestamps preserved;
- standard: 6 requests;
- three-pass: 24 requests.

A 52-unit handoff was generated with a blind Chinese-fluency sheet, an assisted
French-to-Chinese fidelity sheet, and reviewer instructions. The private answer
key was excluded from the handoff archive. The completed review was returned
without access to A/B strategy identity and was then decoded locally with the
private key.

Reviewer type: `llm_assisted_bilingual_review`.

| Review | standard wins | three-pass wins | Ties | Three-pass non-tie rate |
| --- | ---: | ---: | ---: | ---: |
| Chinese fluency | 23 | 24 | 5 | 51.06% |
| French-Chinese fidelity | 18 | 18 | 16 | 50.00% |

The fidelity review marked 19 `none`, 26 `minor`, 5 `major`, and 2 `severe`
units. Across both options it recorded 11 omission, 8 addition, 20
mistranslation, 6 terminology, 0 pronoun, and 4 continuity flags.

Three-pass did not meet the frozen 60% assisted-review threshold in either
review, while standard did not establish a consistent advantage. The assisted
strategy decision is therefore `inconclusive`.

Qualified human French-Chinese fidelity review remains `not_completed`. This
LLM-assisted result supports candidate screening and error discovery only; it
cannot change production defaults or support a quality-improvement claim.
Because no quality defaults are changing, it does not block quality-neutral
packaging, runtime, documentation, privacy, and license preparation.

## Gate Summary

| Gate | Result |
| --- | --- |
| Public-gold campaign execution | Pass |
| Formal gold WER/CER available | Yes |
| ASR model decision | Keep |
| Change resegmentation default | No |
| Enable retry apply mode | No |
| Translation assisted review | Complete |
| Translation human fidelity gate | Not completed |
| Select translation strategy | No — inconclusive |
| Override private-film Stage 3A failure | No |
| Quality-improvement claim | No |
| Quality-neutral Release Prep | Yes |
| Tag or GitHub Release | No |
