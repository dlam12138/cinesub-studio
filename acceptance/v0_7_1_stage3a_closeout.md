# v0.7.1 Stage 3A Closeout

## Closeout Decision

Stage 3A research execution is complete. No evaluated candidate is promoted.

```text
Stage 3A research execution: Complete
Stage 3A promotion result: No candidate promoted
Stage 3A closeout decision: complete_no_promotion

ASR model: keep_current_model_split
Resegment: reject
Retry: keep_dry_run
Translation strategy: keep_current_default
Translation fidelity: not_human_validated

Allows ASR default change: No
Allows translation default change: No
Allows quality-improvement marketing claim: No
Allows quality-neutral Release Prep: Yes
Allows Tag: No
Allows GitHub Release: No
```

This closeout does not erase or rename earlier evidence:

- Original private-film Stage 3A: campaign execution passed; evidence gate
  failed because the available references and qualified review were
  insufficient.
- Public-gold ASR addendum: execution passed with 6/6 gold samples and 40/40
  runs.
- Translation assisted review: complete but inconclusive.
- Research stage: complete.
- Candidate promotion: none.

The public-gold meeting domain does not substitute for the unresolved
private-film evidence gate.

## Reference-Alignment Sanity Audit

The sole pre-closeout method audit passed.

- Reviewed clips: 6.
- Reviewed source groups: 3.
- Coverage: one clip from each of the six formal speaker tracks, spanning
  early, middle, and late positions.
- Reference alignment: Pass.
- Speaker-track identity: Pass.
- Segment boundaries: Pass.
- Normalization consistency: Pass.
- Frozen small/large evaluator input and window assignment: Pass.

The private audit independently traced the local official dataset rows,
reconstructed the prepared audio, verified gold segment projection, reconciled
the frozen candidate hashes, and confirmed that every candidate cue was
assigned once. Audio, transcripts, original identifiers, local paths, and
subtitle text remain private.

## Promotion Findings

No evaluated candidate met the predefined promotion gates under the current
datasets, models, strategies, and review resources.

- Large-v3 improved matched corpus WER by 2.603%, below the predefined 5%
  promotion threshold.
- Large-v3 required about 2.29 times the small-model runtime.
- One of six samples had a severe WER regression.
- Resegmentation changed normalized text in all six samples and therefore
  failed the text-preservation requirement.
- Selective retry planned, executed, and accepted zero candidate windows.
- Translation fluency was nearly tied: three-pass 24, standard 23, ties 5.
- Translation fidelity tied: three-pass 18, standard 18, ties 16.
- Three-pass used 24 requests versus 6 for standard, approximately four times
  the request count.
- Human French-Chinese fidelity validation was not completed.

There is no credible evidence in this campaign for changing an ASR or
translation production default. Under the current evidence and resource
constraints, further parameter experimentation has lower expected value than
its added complexity and evaluation cost.

## Quality-Neutral Release Prep

Stage 3A closeout allows quality-neutral Release Prep because all quality
defaults remain frozen. This is permission to prepare and validate a candidate,
not approval to publish one.

Allowed preparation includes:

- candidate package construction and clean-directory portable startup checks;
- Web, CLI, Electron, FFmpeg, CUDA, and local-model diagnostics;
- task lifecycle, restart recovery, failure recovery, output, and privacy
  checks;
- user documentation, accurate changelog, known limitations, licenses, and
  third-party attribution;
- final release-candidate smoke testing.

Still prohibited:

- changing the ASR model split;
- enabling retry apply by default;
- promoting the evaluated resegmentation behavior;
- making three-pass the translation default;
- claiming improved ASR or translation quality;
- creating a tag or GitHub Release before separate final acceptance.

The permitted release message is limited to reliability, recovery,
configuration identity, diagnostics, evaluation tooling, privacy, and other
evidence-backed behavior. It must state that quality evaluation did not
identify sufficient evidence to change the current ASR model split or
translation defaults.
