# M6.6 Portable Python Runtime Preparation & Dependency Smoke

## Summary

M6.6 prepared a real local portable Python runtime under ignored
`tools/python/`, installed the project dependencies into that runtime, generated
`dist/cinesub-portable/`, and verified that `start_app.bat` can launch the Web
service from the copied release layout.

This milestone is not release candidate packaging. It verifies portable Web
startup and release runtime detection only. Model inference, real paid
translation, release zipping, and release notes are intentionally left for later
milestones.

## Checkpoint

- M6.5 checkpoint tag: `m6.5-portable-runtime-smoke`
- Tag target: `413fc541a03c02666e29cea3b49add1b062b70d2`
- M6.6 branch: `milestone6.6-portable-python-runtime-prep`
- Branch was created from `m6.5-portable-runtime-smoke`.
- `project_evaluation_report.md` remained untracked and was not staged.

## Portable Python input

- Source URL:
  `https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe`
- Reason for version: Python 3.12.13 does not provide binary installers, while
  Python 3.12.10 is the last Python 3.12 full maintenance release with Windows
  installers.
- Download target: `.tmp/python-runtime/python-3.12.10-amd64.exe`
- Local downloaded file size: `26964224` bytes
- Local downloaded file SHA256:
  `67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB`
- Install target: `tools/python/`
- Installer options explicitly avoided PATH changes, launcher installation,
  shortcuts, and file associations.

Runtime verification:

```text
tools/python/python.exe
Python 3.12.10 (tags/v3.12.10:0cc8128, Apr  8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]
pip 26.1.2
```

Dependency smoke:

```text
faster-whisper 1.2.1
ctranslate2 4.8.0
huggingface_hub 0.36.2
portable deps ok
```

Dependencies were installed with `tools/python/python.exe -B -m pip`, not with
the source `.venv`.

## Builder result

Command:

```powershell
.\.venv\Scripts\python.exe -B .\scripts\build_portable_release.py --force
```

Initial result exposed a release builder scanner false positive:

```text
[ERROR] Release leak scan failed:
- runtime/python/Lib/site-packages/huggingface_hub/_oauth.py: secret-looking config value
- runtime/python/Lib/site-packages/huggingface_hub/constants.py: secret-looking config value
- runtime/python/Lib/site-packages/huggingface_hub/inference/_client.py: secret-looking config value
- runtime/python/Lib/site-packages/huggingface_hub/inference/_generated/_async_client.py: secret-looking config value
- runtime/python/Lib/test/test_smtplib.py: secret-looking config value
```

This was a false positive inside copied third-party Python runtime files, not a
real project Provider secret. The builder was updated to skip content leak
scanning inside `runtime/python/` while preserving rejected-file checks and
content scanning for copied app/source files.

Regression coverage was added so app source secrets are still rejected, while
third-party runtime examples under copied Python do not block the release build.

Final builder result:

```text
Portable release prototype: <repo>\dist\cinesub-portable
Python runtime: <repo>\dist\cinesub-portable\runtime\python
FFmpeg copied: yes
Copied files: 9819
Total bytes: 720140966
Leak scan: passed
```

## Release smoke results

The release launcher was started from `dist/cinesub-portable/start_app.bat` with
no existing listener on port `7860`.

Observed Web checks:

```text
home 200
diagnostics 200
effective-config 200
runtime_layout=release
python=<repo>\dist\cinesub-portable\runtime\python\python.exe
python_source=project-portable-python
ffmpeg_source=bundled
diagnostic_summary=warning
```

`effective-config` was used only as an API availability smoke. Provider was
allowed to remain not configured, and no real Provider API key was required or
written into the release directory.

The release Web process was stopped after validation, and port `7860` was clear.

## Success levels

- Level 1: passed. `tools/python/python.exe` and pip run successfully.
- Level 2: passed. `requirements.txt` dependencies installed into
  `tools/python/`.
- Level 3: passed. `dist/cinesub-portable/start_app.bat` started Web
  successfully.
- Level 4: passed. Diagnostics reported `runtime_layout=release`.

## Local guardrail checks

- No model inference was run.
- No model download was requested.
- No real translation smoke was run.
- No real Provider API key was copied, written, logged, staged, or committed.
- Runtime artifacts remained in ignored local paths:
  `tools/python/`, `dist/`, `.tmp/`, `.cache/`, `models/`, `output/`, `work/`,
  and `logs/`.
- The committed scope is limited to this acceptance evidence and the source/test
  fix required for release layout startup.

## Verification

```text
tests/test_portable_release_builder.py: 11 passed
basic imports: imports ok
subtitle_translate self-test: all checks passed
quality_checker self-test: all checks passed
```

## Outcome

M6.6 succeeded. The release chain has moved past the M6.5 blocker: a real local
portable Python runtime can now be supplied through `tools/python/`, copied into
the release layout, and used to launch the Web service from
`dist/cinesub-portable/start_app.bat`.

The next milestone can proceed to M6.7 release candidate packaging, with zip
creation, version/release notes, and final release checklist work kept separate
from this runtime smoke.
