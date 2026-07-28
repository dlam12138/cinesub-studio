# v0.7 quality-neutral Release Prep start record

## User goal

After merging Stage 3A closeout, create a quality-neutral Release Prep branch,
preserve all production quality defaults, and begin runtime, packaging,
documentation, privacy, and license preparation without creating a tag or
GitHub Release.

## Known facts and evidence

- PR #19 was marked Ready only after its method, consistency, privacy,
  attribution, local verification, and two CI jobs passed.
- PR #19 was squash-merged to main as
  `10269a22364c55b8b1c8c628aac3ce1275f7fe3e`.
- The Release Prep branch was created from that exact main SHA.
- `VERSION`, `pyproject.toml`, and `desktop/package.json` still identify the
  formal release as 0.6.2.
- README identifies the new source as an unreleased v0.7.x candidate but does
  not declare an exact v0.7 target version.
- The formal builder deletes `dist/` and names output from `VERSION`; running it
  now would replace the existing formal 0.6.2 artifact with current candidate
  source under the old filename.

## Decision summary

- Do not guess or introduce a v0.7 version number.
- Do not overwrite the existing 0.6.2 release artifacts.
- Treat candidate package construction as pending until an exact target version
  is explicitly fixed across the frozen version contract.
- Continue safe, non-mutating Release Prep checks and quality-neutral user
  documentation.
- Keep tag, GitHub Release, and release approval blocked.

## Operations

- Created branch `codex/v0.7-quality-neutral-release-prep` from main squash SHA
  `10269a22364c55b8b1c8c628aac3ce1275f7fe3e`.
- Validated portable Python imports and formal build inputs without constructing
  a package.
- Confirmed project-local FFmpeg/FFprobe, CUDA runtime inputs, and both small and
  large-v3 model locations.
- Confirmed source runtime diagnostics recommend CUDA on the current machine.
- Started the real Web server on an alternate loopback port, received HTTP 200
  from `/` and `/api/runtime/diagnostics`, and verified cleanup.
- Confirmed an occupied port is rejected with actionable guidance rather than
  silently reusing an unrelated service.
- Confirmed Provider read payloads expose only masked keys.
- Added an Unreleased, quality-neutral changelog and linked it from README.

## Verification

- No ASR, resegmentation, retry, translation, Provider, or Web production
  default was edited.
- No package, tag, GitHub Release, or release approval was created.
- Runtime and packaging-input preflight passed without network downloads.
- Release-focused tests and the full 557-test suite passed.
- The branch has no Python source or test delta. A direct Ruff check of
  unchanged release/runtime files exposed five pre-existing import-order
  findings; they were not modified because this task forbids opportunistic
  cleanup of historical Ruff debt.

## Unresolved and next step

- Fix an explicit target v0.7 version before running the destructive formal
  builder or changing versioned package documentation.
- After that decision, build the portable candidate, extract it to clean Chinese
  and spaced paths, run packaged/runtime/real-media/restart/recovery smoke, scan
  package privacy and licenses, and seek separate final release acceptance.
