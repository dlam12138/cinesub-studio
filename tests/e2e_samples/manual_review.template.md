# CineSub Studio E2E Manual Review

Use this after `e2e_runner.py` finishes. Keep real notes local if they mention
private films or API/provider details.

## Sample

- Sample ID:
- Source file:
- Language Profile:
- Provider ID:
- Expected language:
- Detected language:
- Language probability:
- Forced language:

## Subtitle Counts

- Source entries:
- Chinese entries:
- Bilingual entries:
- Review-needed entries:
- Quality errors:
- Quality warnings:

## ASR Review

- Is the detected language correct?
- Is `source.srt` readable enough for translation?
- Main ASR problems:
- Decision:
  - pass
  - rerun with forced language
  - rerun with another Whisper model
  - adjust VAD / beam size

## Translation Review

- Is `zh.srt` natural as film subtitles?
- Any missing translation, hallucination, or name/place inconsistency?
- Main translation problems:
- Decision:
  - pass
  - adjust translation_style
  - reduce batch size
  - try stronger translation model

## Quality Checker Review

- Did `quality_report.json` catch real problems?
- Are there too many false positives?
- Does `review_needed.srt` reduce manual work?
- Threshold changes needed:

## Final Decision

- Overall result:
  - pass
  - acceptable with manual fixes
  - rerun required
  - pipeline/config fix required
- Next action:
