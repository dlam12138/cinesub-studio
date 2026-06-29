from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure src subdirectories are on sys.path for cross-module imports when run directly
_src = PROJECT_ROOT / "src"
for _sub in ("core", "pipeline", "config", "web", "tools"):
    _subpath = str(_src / _sub)
    if _subpath not in sys.path:
        sys.path.insert(0, _subpath)

from ffmpeg_locator import find_ffmpeg


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
}

AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
}


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent.parent

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    output_dir = (project_root / args.output_dir).resolve()
    model_dir = (project_root / args.model_dir).resolve()
    work_dir = (project_root / args.work_dir).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(project_root / ".cache" / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(project_root / ".cache" / "huggingface" / "hub"))

    audio_path = prepare_audio(input_path, work_dir)
    srt_path = output_dir / f"{input_path.stem}.{args.model}.srt"

    transcribe_to_srt(
        audio_path=audio_path,
        srt_path=srt_path,
        model_name=args.model,
        model_dir=model_dir,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
        local_files_only=args.local_files_only,
        language_profile_id=args.language_profile,
        condition_on_previous_text=not args.no_condition_on_previous_text,
    )

    print(f"Done: {srt_path}")

    # Translation stage
    if args.translate:
        _run_translation(args, srt_path, output_dir)
        mode_tag = "bilingual" if args.translation_mode == "bilingual" else "translated"
        translated_path = output_dir / f"{input_path.stem}.{args.model}.{mode_tag}.{args.target_language}.srt"
        print(f"Done: {translated_path}")

    return 0


def _run_translation(args: argparse.Namespace, srt_path: Path, output_dir: Path) -> None:
    missing = []
    if not args.api_provider:
        missing.append("api_provider")
    if not args.api_base:
        missing.append("api_base")
    if not args.llm_model:
        missing.append("llm_model")

    api_key = args.api_key or os.environ.get("SUBTITLE_LLM_API_KEY", "")
    if not api_key:
        missing.append("api_key (set --api-key or SUBTITLE_LLM_API_KEY env var)")
    if missing:
        raise SystemExit(
            f"ERROR: --translate requires these parameters: {', '.join(missing)}"
        )

    mode_tag = "bilingual" if args.translation_mode == "bilingual" else "translated"
    translated_path = output_dir / f"{Path(args.input).stem}.{args.model}.{mode_tag}.{args.target_language}.srt"

    from subtitle_translate import translate_srt

    translate_srt(
        input_path=srt_path,
        output_path=translated_path,
        api_provider=args.api_provider,
        api_base=args.api_base,
        api_key=api_key,
        llm_model=args.llm_model,
        target_language=args.target_language,
        batch_size=args.translation_batch_size,
        temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        system_prompt=args.translation_prompt,
        context_window=args.context_window,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a video/audio file into an SRT subtitle file with faster-whisper."
    )
    parser.add_argument("input", help="Input video or audio file.")
    parser.add_argument("--model", default="small", help="Whisper model name. Example: tiny, base, small, medium, large-v3.")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"], help="Run device.")
    parser.add_argument(
        "--compute-type",
        default=None,
        help="Compute type. CPU usually uses int8; CUDA usually uses float16.",
    )
    parser.add_argument("--language", default=None, help="Source language code, for example en, ja, ko, zh. Omit for auto detect.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated SRT files.")
    parser.add_argument("--model-dir", default="models", help="Directory for downloaded model files.")
    parser.add_argument("--work-dir", default="work", help="Directory for temporary extracted audio.")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size for decoding.")
    parser.add_argument("--no-vad", action="store_true", help="Disable voice activity detection.")
    parser.add_argument("--no-condition-on-previous-text", action="store_true", help="Disable conditioning on previous text during transcription.")
    parser.add_argument("--local-files-only", action="store_true", help="Do not download models; only use local model files.")

    # Translation options
    parser.add_argument("--translate", action="store_true", help="Enable LLM translation after transcription.")
    parser.add_argument("--api-provider", default="openai-compatible",
                        choices=["openai-compatible", "anthropic"],
                        help="LLM API provider type.")
    parser.add_argument("--api-base", default="", help="LLM API base URL.")
    parser.add_argument("--api-key", default="", help="LLM API key.")
    parser.add_argument("--llm-model", default="", help="LLM model name.")
    parser.add_argument("--target-language", default="zh-CN", help="Target language code.")
    parser.add_argument("--translation-batch-size", type=int, default=20, help="Translation batch size.")
    parser.add_argument("--translation-temperature", type=float, default=0.2, help="Translation temperature.")
    parser.add_argument("--translation-mode", default="bilingual",
                        choices=["bilingual", "translated"],
                        help="Translation output mode.")
    parser.add_argument("--context-window", type=int, default=3, help="Context window size for translation.")
    parser.add_argument("--translation-prompt", default="", help="Custom translation system prompt.")
    parser.add_argument("--language-profile", default="", help="Language Profile ID for ASR and translation settings.")

    args = parser.parse_args()

    # Determine which CLI args were explicitly set (by checking sys.argv)
    raw_argv = [a.split("=")[0] for a in sys.argv[1:]]
    def _explicit(*flags: str) -> bool:
        return any(f in raw_argv for f in flags)

    # Apply Language Profile defaults (CLI explicit args take precedence)
    profile_id = args.language_profile
    if profile_id:
        try:
            from language_profile_store import get_language_profile
            profile = get_language_profile(profile_id)
            if profile:
                asr = profile.get("asr", {})
                if not _explicit("--model") and asr.get("whisper_model"):
                    args.model = asr["whisper_model"]
                if not _explicit("--device") and asr.get("whisper_device"):
                    args.device = asr["whisper_device"]
                if not _explicit("--compute-type") and asr.get("compute_type"):
                    args.compute_type = asr["compute_type"]
                if not _explicit("--language") and asr.get("language"):
                    args.language = asr["language"]
                if not _explicit("--beam-size") and asr.get("beam_size") is not None:
                    args.beam_size = asr["beam_size"]
                if not _explicit("--no-vad") and asr.get("vad_filter") is False:
                    args.no_vad = True
                if not _explicit("--translation-prompt") and profile.get("translation_style"):
                    args.translation_prompt = profile["translation_style"]
                if not _explicit("--target-language") and profile.get("target_language"):
                    args.target_language = profile["target_language"]
            else:
                print(f"Warning: Language Profile '{profile_id}' not found, using CLI args", file=sys.stderr)
        except Exception as exc:
            print(f"Warning: cannot load Language Profile store: {exc}", file=sys.stderr)

    return args


def prepare_audio(input_path: Path, work_dir: Path) -> Path:

    suffix = input_path.suffix.lower()
    if suffix not in VIDEO_EXTENSIONS and suffix not in AUDIO_EXTENSIONS:
        raise SystemExit(f"Unsupported input extension: {suffix}")

    audio_path = work_dir / f"{input_path.stem}.16k.wav"

    if suffix == ".wav":
        convert_to_whisper_wav(input_path, audio_path)
        return audio_path

    convert_to_whisper_wav(input_path, audio_path)
    return audio_path


def convert_to_whisper_wav(input_path: Path, audio_path: Path) -> None:
    ffmpeg = find_ffmpeg(PROJECT_ROOT)
    if ffmpeg is None:
        raise SystemExit(
            "ffmpeg not found.\n"
            "Options:\n"
            "  1. Place bundled ffmpeg.exe in tools/ffmpeg/bin/\n"
            "  2. Run: py src/tools/download_ffmpeg.py  (auto-download to tools/ffmpeg/bin/)\n"
            "  3. Set CINESUB_FFMPEG to the project-local ffmpeg path\n"
            "  4. Install ffmpeg separately only as a fallback"
        )

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(audio_path),
    ]

    print(f"Extracting audio: {audio_path}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("ffmpeg failed to extract audio.")


def transcribe_to_srt(
    *,
    audio_path: Path,
    srt_path: Path,
    model_name: str,
    model_dir: Path,
    device: str,
    compute_type: str | None,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
    local_files_only: bool,
    language_profile_id: str = "",
    language_profile_name: str = "",
    condition_on_previous_text: bool = True,
) -> dict | None:
    """Run Whisper transcription and write SRT.

    Returns language detection info dict (or None on failure):
        {"source_language": "ja", "language_probability": 0.94, "model": "large-v3"}
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit("faster-whisper is not installed. Run .\\install.ps1 first.") from exc

    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    print(f"Loading model: {model_name}")
    print(f"Model dir: {model_dir}")
    print(f"Device: {device}, compute_type: {compute_type}")
    print(f"Local files only: {local_files_only}")

    try:
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(model_dir),
            local_files_only=local_files_only,
        )
    except Exception as exc:
        print("")
        print("Model load failed.")
        print("If this is the first run, the model must be downloaded once.")
        print("Try the web option 'hf-mirror.com' as the model source, or disable 'local only'.")
        print(f"Original error: {exc}")
        raise

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        condition_on_previous_text=condition_on_previous_text,
    )

    detected = getattr(info, "language", None)
    probability = getattr(info, "language_probability", None)
    lang_info = None
    if detected:
        prob_str = f"{probability:.2f}" if probability is not None else "N/A"
        print(f"Detected language: {detected} ({prob_str})")

        # ── 保存语言识别结果为结构化 JSON ──
        lang_info = {
            "source_language": detected,
            "language_probability": round(probability, 4) if probability is not None else None,
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "language_profile": language_profile_id,
            "language_profile_name": language_profile_name,
            "forced_language": language,
            "vad_filter": vad_filter,
            "beam_size": beam_size,
        }

    tmp_srt_path = srt_path.with_name(f"{srt_path.name}.tmp")
    try:
        with tmp_srt_path.open("w", encoding="utf-8") as file:
            for index, segment in enumerate(segments, start=1):
                text = segment.text.strip()
                if not text:
                    continue

                file.write(f"{index}\n")
                file.write(f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n")
                file.write(f"{text}\n\n")
        _replace_output_file(tmp_srt_path, srt_path)
    except Exception:
        try:
            tmp_srt_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    if lang_info is not None:
        lang_json_path = srt_path.with_suffix(".lang.json")
        tmp_lang_json_path = lang_json_path.with_name(f"{lang_json_path.name}.tmp")
        import json as _json
        tmp_lang_json_path.write_text(
            _json.dumps(lang_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _replace_output_file(tmp_lang_json_path, lang_json_path)
        print(f"Language detection saved: {lang_json_path}")

    return lang_info


def _replace_output_file(tmp_path: Path, final_path: Path) -> None:
    """Replace an output file, tolerating stale failed-output files on Windows."""
    try:
        os.replace(tmp_path, final_path)
        return
    except PermissionError:
        if final_path.exists():
            try:
                final_path.chmod(0o666)
            except OSError:
                pass
            try:
                final_path.unlink()
                os.replace(tmp_path, final_path)
            except PermissionError:
                final_path.write_bytes(tmp_path.read_bytes())
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return
        raise


def format_srt_time(seconds: float) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


if __name__ == "__main__":
    raise SystemExit(main())
