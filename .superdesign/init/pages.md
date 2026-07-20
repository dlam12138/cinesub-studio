# Page dependency tree

## `/` — CineSub Studio

Entry: `web/index.html`

Dependencies:

- `web/index.html`
  - Inline CSS theme and all component styles
  - Inline application shell and all tab markup
  - Inline JavaScript for tab state, forms, API calls, task rendering, and dialogs
- `src/web/web_server.py`
  - Serves the HTML and dispatches Web APIs
- `src/web/job_api.py`
  - Single-file form contract and job state
- `src/web/pipeline_api.py`
  - Batch form contract and pipeline state

There are no imported front-end components or external style sheets.
