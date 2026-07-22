# v0.7.1 Real Media Acceptance

## Conclusion

**Conditional pass.** The final release-candidate configuration keeps `quality` on
`large-v3` with word timestamps and deterministic resegmentation, but changes its
fixed retry default from `apply` to `dry_run`.

Automatic retry replacement is **not accepted for release**. Across the three frozen
samples, the apply attempt executed one suspicious-cue window and accepted none. This
is below the frozen minimum of three accepted windows and therefore provides
insufficient evidence that automatic replacement is more trustworthy than the
large-v3 control.

## Attempts

- Attempt 2 found a real French-token defect: zero-duration text tokens were omitted
  before resegmentation. Text-conservation checks prevented output damage and forced a
  fallback. The defect was fixed without weakening conservation checks.
- Attempt 3 verified the fix, produced valid timelines, and evaluated `quality=apply`.
  It accepted 0 windows, so the preset was remediated to `dry_run`.
- Attempt 4 evaluated SHA `dd170e3cbfcb341482558013bbb87e14294f568a` with the
  remediated preset. All 12 cold-process runs completed successfully.

## Final Matrix

| Profile | Model | Word timing | Resegment | Retry | Pipeline ratio | End-to-end ratio |
| --- | --- | --- | --- | --- | ---: | ---: |
| `speed` | small | off | off | off | 1.000 | 1.000 |
| `balanced` | small | on | on | dry-run | 1.003 | 0.991 |
| `large-control` | large-v3 | on | on | off | 4.310 | 3.549 |
| `quality` | large-v3 | on | on | dry-run | 3.824 | 3.203 |

The ratios use summed times across all three samples. Pipeline time excludes FFmpeg
and model loading; end-to-end time includes the complete independent process.

## Acceptance Checks

- All final SRT cues were non-negative, ordered, non-overlapping, and had `end > start`.
- All nine final runs with resegmentation enabled applied it successfully with text and
  word conservation; no final run used a fallback.
- Retry budgets held: one 19.33-second candidate was evaluated for the main low-volume
  sample, while three lower-priority windows were skipped by the 10% duration budget.
- VAD uncovered intervals remained diagnostic-only and were never applied.
- The representative legacy CLI SRT was byte-identical to the `speed` SRT.
- Hotword A/B/C/D did not improve the frozen proper-name targets. Hotwords remain an
  optional explicit field and no default hotword prompt is shipped.
- VideOCR CLI v1.5.1 with local PaddleOCR supplied weak evidence only. Its extracted
  executable was fingerprinted, but the original release archive was unavailable, so
  `archive_verified` remains false.

## Frozen Components

- Base SHA: `ff2f48b754687346410c850ecdf628045056de8c`
- v0.7 implementation SHA: `6d13655d4597b331073ce480f94fbff1f5836a5c`
- Final evaluated SHA: `dd170e3cbfcb341482558013bbb87e14294f568a`
- Acceptance report commit: `2bdea2282f6d858609419e40339c442dd98f4ed5`
- large-v3 revision: `edaa852ec7e145841d8ffdb056a99866b5f0a478`
- large-v3 `model.bin` SHA256: `69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1`
- VideOCR executable SHA256: `7b366641db86c89e3fa559b9f97824040049959e909362ac3b7fec7616337d7c`

Private media, OCR SRT, transcripts, frames, local audits, and absolute paths remain
Git ignored. OCR is not integrated into CineSub Studio.
