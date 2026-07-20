# Local ASR benchmark corpus

Copy `manifest.example.json` to `manifest.local.json`, then place the ten authorized 30–90 second media clips and hand-annotated UTF-8 reference SRT files under `local/`.

Both `manifest.local.json` and `local/` are gitignored. Do not commit media, reference transcripts, generated hypotheses or reports.

Reference rules:

- one spoken utterance per cue;
- no speaker labels or environmental sound descriptions;
- retain audible fillers;
- use `[UNK]` for an uncertain token; benchmark text normalization excludes it;
- cue boundaries should follow the audible utterance rather than an existing translated subtitle.
