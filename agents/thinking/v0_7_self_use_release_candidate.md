# v0.7 self-use release candidate closeout

## User goal

Adopt Route A as a personal-use Windows long-form subtitle application, close
Stage 3 research without promoting a quality candidate, build and validate a
quality-neutral v0.7 portable candidate, and stop before Tag or GitHub Release.

## Known facts and evidence

- PR #19 was squash-merged as
  `10269a22364c55b8b1c8c628aac3ce1275f7fe3e`.
- Stage 3A reference alignment passed, but no ASR, resegmentation, retry, or
  translation candidate met its promotion gate.
- A packaged full-film run exposed two release blockers: the bilingual flow
  did not independently materialize the Chinese-only SRT, and Web processing
  could move an externally selected input into the package archive.
- An attribution audit found a stale release number in the notice heading.
- The final portable ZIP and its internal checksum list both validate.
- Anonymous full-film and interruption-recovery packaged acceptance passed.

## Decisions

- Keep all production quality defaults frozen and describe v0.7.0 as a
  quality-neutral reliability release.
- Generate Chinese-only and bilingual outputs independently from one
  translation response; preserve untranslated source text as an explicit
  review fallback when reliability mode is off.
- Web-launched pipeline work must preserve external inputs; explicit CLI
  archival remains available.
- Expose only a fixed, application-owned output-directory opener to Electron.
- Do not upload the candidate or create a Tag or GitHub Release.

## Operations

- Simplified governance and isolated research-only tooling from the portable
  runtime.
- Updated version contracts, user documentation, changelog, packaged notices,
  and release tests for 0.7.0.
- Fixed the two full-film blockers and added regression coverage.
- Built, extracted, launched, scanned, and checksum-verified the portable
  candidate in a fresh Chinese-and-space path.
- Completed one anonymous long-film pipeline and one packaged interruption /
  explicit recovery smoke.
- Added public anonymous acceptance Markdown and JSON.

## Verification

- Full suite: 560 tests passed.
- Official imports, self-tests, Web smoke, HTTP diagnostics, Electron syntax,
  and diff checks passed.
- Runtime/code SHA Windows CI passed.
- Final ZIP size is 2,076,877,448 bytes; SHA256 is
  `687a9efa20493fe220163f5c58186be2ef27155c0769b9289b8cf0dbb0d326d8`.
- Package checksum verification covered 9,284 entries with 0 mismatches.
- Package privacy, dependency, local-path, attribution, and research-data
  scans passed.

## Unresolved and next step

- Generated subtitles still require human review; two cues in the accepted
  film were intentionally surfaced as source-text fallbacks.
- Repository Ruff retains 176 pre-existing findings; this release task did not
  expand scope into historical cleanup.
- A Tag proposal is allowed, but Tag creation, artifact upload, and GitHub
  Release require a separate explicit approval.
