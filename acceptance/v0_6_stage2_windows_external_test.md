# v0.6 Stage 2 Windows External Test — Acceptance

## Scope

- Separate Windows x64 CPU/auto and GPU NSIS builds.
- Dynamic app version/flavor identity via read-only `/api/app-info`.
- Formal offline brand assets and locally bundled UI fonts.
- First-run readiness flow, unified async states and timeline-style overall progress.
- No auto-update, code-signing service, bundled models, silent downloads, TTS/dubbing or ASS output.

## Automated Validation

Validated locally on 2026-07-10:

- Python/Node syntax and base imports: passed.
- Translation and quality-check self-tests: passed.
- Full pytest suite: `395 passed`, with `PytestUnhandledThreadExceptionWarning` promoted to error.
- Source Web smoke: `/`, `/api/runtime/diagnostics`, `/api/app-info` returned 200.
- CPU and GPU unpacked/NSIS builds: passed; both generated `release_manifest.json`.
- Browser visual QA: 1440px desktop and 820px narrow viewport passed; screenshots retained under ignored `output/playwright/`.

## Local Build Evidence

| Flavor | Unpacked | NSIS | CUDA policy | Packaged smoke | Artifact size | SHA-256 |
| --- | --- | --- | --- | --- | ---: | --- |
| CPU/auto | passed | passed | absent | homepage/diagnostics/app-info 200 | 261.2 MB | `71B556351852F04921472337425512F0CBA0784B9AB5CD86E3D3744706EBF35A` |
| GPU | passed | passed | present | homepage/diagnostics/app-info 200 | 1207.4 MB | `2A367FB6200EC5655275604ED89ED27B90380A1793AE2C352D44EE23F70A6555` |

Artifacts:

- `desktop/release/cpu/CineSubStudio-0.6.0-windows-x64-cpu-setup.exe`
- `desktop/release/gpu/CineSubStudio-0.6.0-windows-x64-gpu-setup.exe`

The first GPU unpacked attempt exposed a PowerShell switch-forwarding bug: CUDA precheck passed but the collector did not receive `-RequireCuda`. Manifest generation rejected the CUDA-free GPU staging. The collector now receives a named-parameter hashtable; the full GPU unpacked and NSIS builds then passed.

## Short Sample

- Input: `tests/e2e_samples/fr_short/34584660077-1-192.mp4`; model: existing local `small`; forced source language: `fr`; translation disabled.
- CPU packaged runtime: `device=auto` emitted the expected CUDA-unavailable warning, fell back to `cpu/int8`, kept `Local files only: True`, and produced a 25,862-byte source SRT in about 241 seconds.
- GPU packaged runtime: explicit `device=cuda` selected `cuda/float16`, kept `Local files only: True`, and produced a 29,811-byte source SRT in about 41 seconds.
- Neither run downloaded a model or invoked an LLM API. Outputs are retained only under ignored `.tmp/stage2-sample-*-v060/`.

## Clean Windows 10/11 VM Checklist

This repository has no available clean VM, so these items remain deliberately unchecked:

- [ ] VM has no system Python, Node/npm or FFmpeg.
- [ ] CPU installer installs, starts from Start Menu and returns 200 for homepage and diagnostics.
- [ ] GPU installer installs and clearly diagnoses missing/incompatible NVIDIA driver.
- [ ] Folder picker, Provider and Language Profile pages open.
- [ ] Closing Electron terminates the backend process tree.
- [ ] Uninstall removes application files without deleting user subtitles/configuration.
- [ ] No unexpected PATH, proxy, system Python or global cache changes.

## Exception Closeout Decision

Stage two was closed as `completed` by explicit exception on 2026-07-10 so that the ASR benchmark work can proceed. The unchecked VM items above remain deferred and are **not** treated as passed.

- Reason: the current host is Windows Home and has no supported Windows Sandbox, VMware or VirtualBox environment available for a clean-machine run.
- Accepted evidence: local automated regression, CPU/GPU unpacked and NSIS builds, packaged smoke, diagnostics, browser QA and CPU/GPU short-sample transcription.
- Residual risk: installation, first launch, driver diagnostics, process-tree shutdown and uninstall/data-preservation behavior have not been proven on a machine without system Python, Node or FFmpeg.
- Recovery trigger: complete and record this checklist before a stable public release or any external claim that the installer is zero-configuration compatible.
- Current CPU artifact SHA-256: `71B556351852F04921472337425512F0CBA0784B9AB5CD86E3D3744706EBF35A`.
