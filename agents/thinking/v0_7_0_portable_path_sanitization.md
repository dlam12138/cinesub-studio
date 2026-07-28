# v0.7.0 portable Python path sanitization

## User goal

Rebuild the final v0.7.0 candidate from squash-merged `main`, require a clean
package privacy/path audit, and prepare—but do not create—the release tag.

## Known facts and evidence

- PR #20 was squash-merged as
  `7d76d8d300e170534c3d67e54920df4a2080dbb8`.
- The first clean-main rebuild completed and all 9,284 manifest checksums
  matched.
- A binary content scan found the build-machine path embedded in 20
  `resources/app/python/Scripts/*.exe` distlib console launchers.
- Those launchers point to the Python installation used to create them and are
  not relocatable.
- The packaged application starts Python through
  `resources/app/python/python.exe` and imports runtime modules directly; it
  does not call those console entry points.

## Decision

- Treat the path disclosure as a release blocker.
- Remove portable Python `Scripts/` while staging the runtime.
- Make the final package scanner reject that directory if it reappears.
- Do not weaken the no-local-path gate and do not continue to Tag proposal
  until the corrected package is merged, rebuilt, and revalidated.

## Operations

- Added runtime staging removal for non-relocatable console launchers.
- Added a package-scan hard rejection and regression coverage.
- Did not change ASR, translation, model, retry, resegmentation, Provider, or
  Language Profile defaults.

## Verification and next step

- Run targeted and full repository verification.
- Merge the minimal blocker fix through CI.
- Rebuild from the new clean `main`, rescan package bytes, and then resume
  fresh-directory startup and short-media smoke.
