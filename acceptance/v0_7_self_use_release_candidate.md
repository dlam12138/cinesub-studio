# v0.7 self-use release candidate acceptance

## Scope

This report closes the quality-neutral Release Prep requested for the
personal-use Windows long-form subtitle product direction. It records only
anonymous aggregates. Private media, subtitle text, local paths, Provider
secrets, and detailed run artifacts remain outside Git.

Evaluated source:

- Branch: `codex/v0.7-self-use-release-prep`
- Base SHA: `10269a22364c55b8b1c8c628aac3ce1275f7fe3e`
- Runtime/code SHA: `654b49314fc16bd160191a24b876d9df8fd13429`
- Target version: `0.7.0`

## Stage 3A closeout

- Reference-alignment sanity: `pass` (6 clips, 3 anonymous source groups).
- PR #19 squash SHA:
  `10269a22364c55b8b1c8c628aac3ce1275f7fe3e`.
- Research execution: `complete`.
- Promotion result: `complete_no_promotion`.
- ASR model: `keep_current_model_split`.
- Resegmentation: `reject`.
- Selective retry: `keep_dry_run`.
- Translation strategy: `keep_current_default`.
- Human French-Chinese fidelity gate: `not_completed`.

No evaluated candidate met the predefined promotion gates under the current
datasets, models, strategies, and review resources. Production quality
defaults remain frozen and no quality-improvement claim is made.

## Product and packaging boundary

Route A is active: CineSub Studio is maintained primarily as a personal-use
Windows long-form subtitle application. Governance now reserves formal
acceptance and thinking records for user-visible, release-blocking, migration,
or major architectural work. Stage 3 campaign, public-gold, JiWER,
SacreBLEU, and blind-review tooling is explicitly research-only and excluded
from the portable runtime.

The candidate is a single top-level portable ZIP:

- Artifact: `CineSubStudio-0.7.0-windows-x64-portable.zip`
- Size: 2,076,877,448 bytes
- SHA256:
  `687a9efa20493fe220163f5c58186be2ef27155c0769b9289b8cf0dbb0d326d8`
- Split CUDA add-on: no
- Bundled: Electron, portable Python, FFmpeg/FFprobe, CUDA runtime, required
  Python packages, and the `small` model.
- Manifest payload: 9,283 files and 3,329,205,786 bytes.
- Internal checksum verification: 9,284 entries checked, 0 mismatches.

## Fresh-directory packaged startup

The ZIP was extracted into a new path containing Chinese characters and
spaces, outside the development checkout.

- Direct EXE launch: `pass`.
- Home page and packaged diagnostics: HTTP 200.
- Version and layout: `0.7.0`, packaged Electron portable.
- System Python dependency: none.
- System FFmpeg PATH dependency: none; bundled FFmpeg was selected.
- CUDA diagnostics: available and recommended.
- `small` model: discovered and loaded with CUDA/float16 and
  `local_files_only=True`.
- Provider save/read: `pass`; reads returned only a masked key.
- Runtime writes: confined to the candidate's own `data/` directory.
- Normal application close: Electron, backend, and child processes exited;
  the loopback port was released.
- External subtitle collaboration: the scoped “open output directory” entry
  targets only the application output directory.

## Anonymous real self-use acceptance

One private long-form film was processed with the packaged candidate. It was
not used for model comparison or a quality campaign.

- Duration: 2,705.87 seconds.
- Final run status: `completed`.
- Cue count: 534.
- Stage durations:
  - audio extraction: 3.065 seconds;
  - ASR: 111.200 seconds;
  - translation: 479.744 seconds;
  - quality check: 0.105 seconds.
- Source-language SRT: non-empty, 34,014 bytes.
- Chinese SRT: non-empty, 34,935 bytes.
- Bilingual SRT: non-empty, 48,732 bytes.
- The three outputs have distinct hashes and identical cue IDs/timestamps.
- All timestamps are valid and within the media duration.
- Two untranslated cues were preserved as source-text fallbacks and surfaced
  for review instead of failing the whole film.
- Quality report: `warning`, 125 review issues, 0 errors.
- Every exposed subtitle/report download returned HTTP 200 and non-empty data.
- The original input remained in its selected location; the packaged Web run
  archived 0 external input files.
- Logs contained neither Provider secrets nor subtitle text.

This is a reliability acceptance, not evidence of professional subtitle
quality. The generated subtitles still require human review.

## Packaged interruption and recovery smoke

A separate 908.88-second private medium-length input was interrupted by a
normal application close during ASR.

- The original packaged worker tree was terminated on close; no orphan
  remained.
- After restart, the interrupted run was shown as `stale` with an explicit
  warning and was not silently reset.
- Planning the same task exposed an explicit rebuild path from ASR with no
  blocker.
- Starting that recovery path created exactly one worker and one running task.
- The recovered run completed with 0 failed and 0 stale tasks.

The packaged behavior is explicit recovery, not automatic retry of stale
state.

## Privacy, dependency, and attribution audit

- API keys or secret patterns: 0.
- Private media or subtitle artifacts in the ZIP: 0.
- Private/local absolute path disclosures: 0.
- Git metadata, work records, caches, or logs in the built payload: 0.
- SUMM-RE, MediaSpeech, campaign, or blind-review data: 0.
- Forbidden development/test content: 0.
- Electron, FFmpeg, CUDA, faster-whisper, and WenYi attribution: present.
- Stale version string in packaged third-party notices: absent.

## Verification

- Full pytest: 560 passed.
- Official import check: passed.
- Translation and quality self-tests: passed.
- Web smoke and HTTP diagnostics checks: passed.
- Electron JavaScript syntax checks: passed.
- Candidate checksum, privacy, dependency, path, and attribution scans: passed.
- Changed-file Ruff: no new finding introduced by this work.
- Repository Ruff baseline: 176 pre-existing findings; no opportunistic
  cleanup was included.
- `git diff --check`: passed.
- Windows CI for runtime/code SHA: passed
  ([run 30339687606](https://github.com/dlam12138/cinesub-studio/actions/runs/30339687606)).

## Gate

```text
Route A: Active
Stage 3 research: Closed
Production quality defaults: Frozen
v0.7 self-use release candidate: Ready

Release Prep: Pass
Allows Tag Proposal: Yes
Allows GitHub Release: No

Tag: Not created
GitHub Release: Not created
```

Tagging and publishing require a separate explicit approval.
