# Translation quality benchmark

Keep only small, authorized subtitle samples here and set `authorized: true` in
the local manifest. Real benchmark media, subtitles, generated reviews, and
results should remain ignored local artifacts.

Commands:

```powershell
.\.venv\Scripts\python.exe -B src\tools\translation_quality_benchmark.py evaluate tests\translation_benchmark\manifest.local.json
.\.venv\Scripts\python.exe -B src\tools\translation_quality_benchmark.py blind tests\translation_benchmark\manifest.local.json work\translation-review.json
.\.venv\Scripts\python.exe -B src\tools\translation_quality_benchmark.py score-blind work\translation-review.json
```

Promotion requires at least 60% three-pass preference among non-ties and no
category with more losses than wins. Automated scores do not replace blind
review.
