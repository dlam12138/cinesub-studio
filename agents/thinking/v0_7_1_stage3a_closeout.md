# v0.7.1 Stage 3A closeout record

## User goal

Close Stage 3A without further ASR or translation tuning, preserve all negative
and insufficient-evidence conclusions, and permit only quality-neutral Release
Prep after a reference-alignment sanity audit.

## Known facts and evidence

- The private-film campaign execution passed, but its evidence gate remains
  failed for insufficient evidence.
- Public-gold ASR completed 40/40 runs on 6/6 gold-verbatim samples.
- The translation assisted review is complete but inconclusive; qualified human
  French-Chinese fidelity validation is not completed.
- No ASR or translation production default changed on this branch.
- The private alignment audit reviewed six clips across three source groups.
- The audit independently matched official speaker rows, reconstructed all 52
  prepared source clips byte-for-byte, verified the gold projection, reconciled
  frozen candidate hashes, and confirmed complete cue window assignment.

## Decision summary

- Reference-alignment sanity: Pass.
- Stage 3A research execution: Complete.
- Candidate promotion: None.
- ASR model: `keep_current_model_split`.
- Resegmentation: `reject`.
- Retry: `keep_dry_run`.
- Translation: `keep_current_default`; fidelity not human validated.
- Quality-improvement claims remain prohibited.
- Quality-neutral Release Prep is allowed; tag, GitHub Release, and release
  approval remain blocked pending separate final acceptance.

## Operations

- Read the existing private source, prepared, campaign, evaluator, and
  translation-review artifacts without rerunning ASR, translation, API calls,
  or blind review.
- Created the ignored private reference-alignment sanity result.
- Updated the public-gold report with the passed alignment audit and corrected
  quality-neutral Release Prep semantics.
- Added anonymous Closeout Markdown and JSON plus exact acceptance allowlists.

## Verification

- Six selected clips cover all six formal speaker tracks and early, middle, and
  late bundle positions.
- All selected clips contain non-silent signal and overlapping frozen small and
  large candidate cues.
- Public closeout files contain no media, transcript, translation, original
  source identity, local path, API material, prompt, or answer-key mapping.

## Unresolved and next step

- Run the complete repository verification and CI.
- Mark PR #19 Ready only after all closeout consistency, privacy, attribution,
  clean-worktree, and CI gates pass.
- Squash merge PR #19, then create the quality-neutral Release Prep branch.
- Do not create a tag or GitHub Release in this task.
