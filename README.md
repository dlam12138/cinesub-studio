# CineSub Studio

CineSub Studio is a local subtitle workflow for video and audio files. It uses `faster-whisper` to generate SRT subtitles, then can optionally call an LLM API to produce bilingual or translated-only subtitles.

The project is designed for Windows and keeps virtual environments, model files, uploads, temporary audio, and caches inside the project directory instead of the C drive.

## Features

- Local Web UI at `http://127.0.0.1:7860`
- CLI transcription with `faster-whisper`
- Optional LLM translation after transcription
- OpenAI-compatible Chat Completions API support
- Anthropic Claude Messages API support
- Bilingual SRT output: original text plus translation
- Translation-only SRT output
- Batch translation with context window
- Translation retry and local batch cache for unstable API connections

## Project Structure

- `transcribe.py`: extract audio, run faster-whisper, write source SRT, optionally translate.
- `subtitle_translate.py`: parse SRT, call LLM APIs, write translated or bilingual SRT.
- `web_server.py`: local Web backend for upload, local path jobs, status, logs, and downloads.
- `web/index.html`: single-file static Web frontend.
- `run_transcribe.ps1`: CLI transcription helper.
- `start_web.ps1`: Web UI startup helper.
- `install.ps1`: create or rebuild `.venv` and install dependencies.
- `download_model_file.py`: fallback model-file downloader.
- `AGENTS.md`: rules for future coding agents.

Runtime directories are ignored by Git: `.venv/`, `.cache/`, `models/`, `uploads/`, `work/`, and `output/`.

## Install

```powershell
cd D:\Claude项目操作\电影翻译
.\install.ps1
```

Python 3.12 is recommended when available:

```powershell
.\install.ps1 -Python py -PythonArgs "-3.12" -Recreate
```

## Start Web UI

```powershell
cd D:\Claude项目操作\电影翻译
.\start_web.ps1
```

Open:

```text
http://127.0.0.1:7860
```

## CLI Transcription

```powershell
.\run_transcribe.ps1 -InputFile "D:\Movies\movie.mp4" -Model small -Device cpu
```

## Translate Existing SRT

Prefer passing the API key through an environment variable so it does not appear in the command line.

```powershell
$env:SUBTITLE_LLM_API_KEY="your-api-key"

.\.venv\Scripts\python.exe -B subtitle_translate.py `
  "output\movie.small.srt" `
  "output\movie.small.bilingual.zh-CN.srt" `
  --api-provider openai-compatible `
  --api-base "https://api.deepseek.com/v1" `
  --llm-model "deepseek-chat" `
  --target-language zh-CN `
  --translation-mode bilingual `
  --context-window 3 `
  --translation-batch-size 5
```

For Anthropic-compatible endpoints:

```powershell
.\.venv\Scripts\python.exe -B subtitle_translate.py `
  "output\movie.small.srt" `
  "output\movie.small.bilingual.zh-CN.srt" `
  --api-provider anthropic `
  --api-base "https://api.anthropic.com/v1" `
  --llm-model "claude-3-5-sonnet-latest" `
  --target-language zh-CN `
  --translation-mode bilingual `
  --context-window 3 `
  --translation-batch-size 5
```

## Checks

```powershell
.\.venv\Scripts\python.exe -B -c "import transcribe, web_server, download_model_file, subtitle_translate; print('imports ok')"
```

```powershell
.\.venv\Scripts\python.exe -B subtitle_translate.py --self-test
```

## Notes

- Do not commit API keys.
- Do not commit `.venv/`, `.cache/`, `models/`, `uploads/`, `work/`, or `output/`.
- Model downloads can be large. Keep them in `models/` and outside Git.
- Uploaded media and extracted audio can be large. Clean `uploads/` and `work/` after jobs finish if disk space matters.
