# CineSub Studio design system

## Product and job

CineSub Studio is a local-first Windows workstation for people producing
translated movie subtitles. The batch and single-file pages have one job:
let the user choose input, choose one of three ASR modes, optionally translate,
and start with confidence.

## Visual rules

- Keep the existing graphite `#090d14` / `#111823` workstation shell.
- Use `#63b3ff` for selection and primary actions, `#49d18d` for ready/success,
  `#ff6b6b` for blocking errors, and `#9aa8ba` for secondary text.
- Use local Chinese system sans-serif fonts; never add remote fonts.
- Keep the existing 8px radius and restrained border/shadow treatment.
- Primary controls are at least 44px high; form groups use 16–20px spacing.
- Preserve the fixed desktop sidebar and responsive stacked narrow layout.

## Signature component

The ASR mode selector is a three-card audio-routing rail. Each card contains a
plain Chinese mode name, one-sentence outcome, and a small flow cue:

- 自动检测: 整片 → 自动识别
- 固定单语言: 整片 → 指定语言
- 多语言: VAD 分块 → 分块识别 → 合并

Only one card is selected. The fixed-language select appears directly below the
rail and only when that mode is active.

## Content rules

- Chinese-first UI; no disabled English-language switch.
- Keep unavoidable technical names such as CPU, CUDA, FFmpeg, SRT, and model IDs,
  with Chinese context.
- Do not expose recognizer, aligner, candidate routing, confidence thresholds, or
  custom prompt editors in the product UI.
- Errors state the corrective action first and technical stderr/return code second.
