from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


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
    project_root = Path(__file__).resolve().parent

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
    return parser.parse_args()


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
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg was not found in PATH.")

    command = [
        "ffmpeg",
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
) -> None:
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
    )

    detected = getattr(info, "language", None)
    probability = getattr(info, "language_probability", None)
    if detected:
        print(f"Detected language: {detected} ({probability:.2f})" if probability is not None else f"Detected language: {detected}")

    with srt_path.open("w", encoding="utf-8") as file:
        for index, segment in enumerate(segments, start=1):
            text = segment.text.strip()
            if not text:
                continue

            file.write(f"{index}\n")
            file.write(f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n")
            file.write(f"{text}\n\n")


def format_srt_time(seconds: float) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


if __name__ == "__main__":
    raise SystemExit(main())
