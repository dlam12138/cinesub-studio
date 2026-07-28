# v0.7.1 Stage 3A Public Gold Addendum work record

## User goal

Add auditable public-gold French ASR evidence, preserve the prior private-film
failure record, run matched controls, prepare translation blind-review material,
and keep production and release gates closed unless the evidence supports them.

## Facts and evidence

- The prior private-film Stage 3A result remains unchanged.
- MediaSpeech FR did not expose a recoverable three-group source identity in the
  downloaded public archive, so it was rejected for this matched protocol.
- SUMM-RE `dev` at fixed revision
  `6b5492d1cea1e483131627c939f82c3989c52b0d` supplied three source groups with
  two speaker tracks each. No train data was used.
- Six `gold_verbatim` samples totaling 295.994 seconds were prepared.
- All 40 frozen ASR runs completed at source SHA
  `ec416ef18cf531e93a562a65894bc0ba5201ca15`.
- The large-v3 corpus WER improvement was 2.603%, below the frozen 5% promotion
  threshold, with one severe per-sample regression and higher runtime/risk counts.
- Resegmentation changed normalized text on all six samples.
- Retry planned, executed, and accepted zero windows.
- Twelve translation candidates preserve all 52 cue IDs and timestamps.

## Decisions

- Keep the current ASR model split.
- Reject a resegmentation default change.
- Keep selective retry in dry-run mode.
- Leave translation strategy inconclusive until assisted and human fidelity
  review is completed.
- Do not authorize production default changes or Release Prep.

## Operations

- Added public-gold preparation, evaluation, and translation-review tooling with
  regression tests.
- Added resumable, bounded SUMM-RE selected-shard handling.
- Executed the 40-run ASR campaign and generated private detailed evidence.
- Generated standard and three-pass translation candidates.
- Built a three-file blind-review handoff archive with the answer key outside it.
- Added anonymous public Markdown, JSON, and CSV evidence.

## Verification

- Tooling and regression tests passed before the campaign.
- ASR campaign status: 40/40 successful with no independent decode variance.
- Translation structure check: 6/6 samples and 52/52 cues matched.
- Handoff archive contains only the two review sheets and reviewer instructions.

## Unresolved and next step

- Assisted review is incomplete.
- Qualified human French-Chinese fidelity review is not completed.
- The reviewer must fill and return the handoff before a translation evidence
  commit can select or reject a strategy.
- Public-gold meeting speech does not substitute for the failed private-film
  evidence gate.
