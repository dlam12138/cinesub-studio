# v0.7.1 Real Media Acceptance

## Scope

This acceptance evaluates whether the fixed `local-retry-selective-v2` recipe improves
real-media ASR output without breaking subtitle timing or text coverage. It does not add
an OCR dependency to CineSub Studio and does not change the product output.

## OCR Evidence

- Primary hard-subtitle evidence tool: VideOCR CLI v1.5.1.
- Source: `timminator/VideOCR` official GitHub release.
- Frozen upstream source: `ab4599dd8d55978ee2b29169c9e1b40dd0bae316`.
- Engine: local PaddleOCR only; cloud OCR is prohibited.
- Language: French (`fr`).
- Each sample uses a frozen time window and pixel crop rectangle.
- OCR SRT, logs, source media, frames, and full transcripts remain private and Git ignored.
- OCR remains weak evidence and cannot automatically accept or reject an ASR retry window.
- Windows OCR may be recorded as optional secondary evidence when its requested language
  engine is available, but it is not an acceptance prerequisite.

## Reproducibility

The private freeze record contains the VideOCR executable hash, official release archive
hash, crop coordinates, time windows, model fingerprints, environment versions, and the
evaluated Git SHA. Public reports contain only anonymous sample IDs, aggregate metrics,
irreversible text hashes, and review classifications.
