# v0.7.1 Stage 3A French Quality Campaign

## Conclusion

**Stage 3A: Fail.** The campaign itself executed successfully, but the available
evidence is insufficient to promote an ASR model, change resegmentation defaults,
or select a translation strategy. All six ASR references are weak local OCR rather
than human-verified verbatim transcripts, and the translation blind review has no
qualified bilingual reviewer or authorized Chinese reference.

- Validated scope: French film samples.
- Not validated in this campaign: English, Mandarin, real code-switching.
- ASR model decision: `inconclusive`.
- ASR resegment decision: `inconclusive`.
- ASR retry decision: `keep_dry_run`.
- Translation strategy decision: `inconclusive`.
- Allows Stage 3B: Yes.
- Allows Release Prep: No.

This result does not claim that ASR or translation quality improved.

## Scope and References

The frozen set contains six non-overlapping windows from three authorized French
film sources, with two windows per source group. The windows cover clear near-field
speech, continuity, low-volume dialogue, and far-field difficult speech. Total
evaluated media duration is 720 seconds.

Reference inventory:

- `gold_verbatim`: 0
- `production_subtitle`: 0
- `ocr_weak`: 6

Consequently, formal WER, CER, substitution, insertion, deletion, and ASR text
accuracy comparisons are unavailable. OCR was used only for weak timing and
coverage support.

## Frozen ASR Campaign

- ASR evaluated SHA: `e0be73e73c681c7f0eefa4ba19ad5ecdc080643b`
- Scope: `stage3a_french_film`
- Runs: 28 unique, 28 successful
- Primary matrix: 6 samples × 4 profiles
- Contract controls: 4
- Runtime: CUDA, float16, local files only

All seven quality/control pairs matched their frozen input, decode configuration,
runtime configuration, and allowed profile difference. Every quality dry-run
accepted zero retry windows, so no retry output was applied.

One of seven independent quality/control pairs produced different SRT hashes on a
difficult far-field sample. A same-profile repeat also varied, isolating this as
independent CUDA decode variance rather than retry application. The contract status
is therefore `pass_with_decode_variance`, not fully reproducible pass.

## Aggregate Structural Evidence

| Profile | Runs | End-to-end seconds | Cues | Gap seconds | Over 20 CPS | Over 42 chars |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `speed` | 6 | 48.614 | 159 | 31.17 | 15 | 117 |
| `balanced` | 6 | 41.257 | 197 | 85.39 | 56 | 95 |
| `large-control` | 6 | 171.609 | 200 | 81.99 | 51 | 92 |
| `quality` | 6 | 187.898 | 201 | 81.21 | 51 | 92 |

Resegmentation reduced very long subtitle lines in aggregate, but increased
high-CPS cues and total gaps. Without completed human readability review, this is a
mixed structural result and does not justify a default change.

`large-v3` was materially slower than the small-model profiles. Because no
gold-verbatim or completed French blind review exists, model size and structural
counts cannot establish text-accuracy superiority.

No suspicious retry window was planned, executed, or accepted across the six
quality runs. The selective retry recipe therefore remains `dry_run`; `apply`
is not authorized.

## Translation T1

After ASR completion, the six quality-profile SRT files were frozen and translated
with the same Provider configuration into:

- 6 `standard` candidates
- 6 `three_pass` candidates

All 12 translated-only SRT files preserved cue IDs and timestamps, contained no
empty cues, and passed the wrapper/structure checks. A deterministic private blind
review sheet and private answer key were generated.

The blind review is unfilled. No authorized Chinese reference subtitle or qualified
French bilingual review was available. Therefore both Chinese fluency/readability
preference and translation fidelity remain `inconclusive`; candidate translations
were not used as reference answers.

## Required Remediation

Release preparation remains blocked until, at minimum:

1. A representative subset receives authorized, human-verified French verbatim
   transcripts for formal ASR comparison.
2. The ASR model and resegmentation blind review is completed by a qualified
   French reviewer.
3. Translation T1 receives an authorized Chinese reference or qualified French-
   Chinese fidelity review, plus completed blind readability scoring.

Stage 3B may proceed independently and is not blocked by this Stage 3A evidence.
No PR, merge, tag, or release is created by this campaign.
