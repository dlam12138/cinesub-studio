# Routes

The Web UI is a single page served at `/` from `web/index.html`. Navigation is
implemented by tab buttons rather than URL routes.

| Tab | Element | Purpose |
| --- | --- | --- |
| Batch processing | `#tab-pipeline` | Scan, run, monitor, and review directory jobs |
| Single processing | `#tab-transcribe` | Configure and submit one local/uploaded video |
| Recent jobs | `#tab-jobs` | Inspect and retry single-file jobs |
| Runtime | `#tab-runtime` | Diagnose Python, FFmpeg, CUDA, and local models |
| Providers | `#tab-providers` | Manage translation providers |
| Language profiles | `#tab-langprofiles` | Manage ASR/translation/quality profiles |

The shared sidebar and topbar remain mounted while the active tab changes.
