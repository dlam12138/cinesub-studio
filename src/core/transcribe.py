from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from importlib.metadata import version as package_version
from types import SimpleNamespace
from pathlib import Path
from typing import Any


# Ensure src subdirectories are on sys.path for cross-module imports when run directly
_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "pipeline", "config", "web", "tools"):
    _subpath = str(_src / _sub)
    if _subpath not in sys.path:
        sys.path.insert(0, _subpath)

from encoding_utils import run_text, write_json
from ffmpeg_locator import find_ffmpeg
from runtime_env import add_project_cuda_to_process, choose_device, default_compute_type
from runtime_paths import resolve_runtime_paths
from asr_model_locator import locate_asr_model
from asr_runtime import (
    ASR_RETRY_RECIPE_VERSION,
    AsrDecodeOptions,
    TranscriptionArtifact,
    TranscriptionCue,
    TranscriptionWord,
    deduplicate_boundary_cues,
    normalize_asr_request,
    normalize_asr_retry_mode,
    plan_vad_blocks,
    resolve_quality_loop_config,
    suspicious_cue_indexes,
    uncovered_speech_intervals,
)
from asr_retry import (
    build_retry_report,
    empty_retry_report,
    merge_retry_artifact,
    plan_retry_windows,
    select_retry_window,
)
from subtitle_model import (
    ASS_RESERVED_MESSAGE,
    DEFAULT_ASS_STYLE_ID,
    normalize_subtitle_formats,
)
from subtitle_resegment import SubtitleResegmenter

PATHS = resolve_runtime_paths(Path(__file__).resolve())
PROJECT_ROOT = PATHS.project_root
SRC_ROOT = PATHS.src_root


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


@dataclass(frozen=True)
class AsrSession:
    model: Any
    model_name: str
    device: str
    compute_type: str
    model_dir: Path
    local_files_only: bool


def _resolve_local_model_source(model_name: str, model_dir: Path) -> str:
    location = locate_asr_model(
        model_name,
        model_dir,
        PATHS.cache_dir / "huggingface" / "hub",
    )
    return location.local_path if location.available else model_name


def create_asr_session(
    *, model_name: str, model_dir: Path, device: str, compute_type: str | None,
    local_files_only: bool,
) -> AsrSession:
    try:
        add_project_cuda_to_process()
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit("faster-whisper is not installed. Run .\\install.ps1 first.") from exc
    try:
        resolved_device, warnings = choose_device(device)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    for warning in warnings:
        print(f"Warning: {warning}")
    resolved_compute_type = default_compute_type(resolved_device, compute_type)
    model_source = _resolve_local_model_source(model_name, model_dir)
    if local_files_only and model_source == model_name:
        raise SystemExit(
            f"ASR model '{model_name}' is not available locally. "
            f"Import it into {model_dir} or confirm its download in the Web UI."
        )
    print(f"Loading model: {model_name}")
    if model_source != model_name:
        print(f"Using bundled model: {model_source}")
    print(f"Model dir: {model_dir}")
    print(f"Device: {resolved_device}, compute_type: {resolved_compute_type}")
    print(f"Local files only: {local_files_only}")
    try:
        model = WhisperModel(
            model_source, device=resolved_device, compute_type=resolved_compute_type,
            download_root=str(model_dir), local_files_only=local_files_only,
        )
    except Exception as exc:
        print("\nModel load failed.")
        print("If this is the first run, the model must be downloaded once.")
        print("Try the web option 'hf-mirror.com' as the model source, or disable 'local only'.")
        print(f"Original error: {exc}")
        raise
    return AsrSession(model, model_name, resolved_device, resolved_compute_type, model_dir, local_files_only)


def main() -> int:
    process_started = time.perf_counter()
    args = parse_args()
    project_root = PROJECT_ROOT
    subtitle_formats = normalize_subtitle_formats(args.subtitle_formats)
    args.subtitle_formats = ",".join(subtitle_formats)
    args.ass_style_id = args.ass_style_id or DEFAULT_ASS_STYLE_ID

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

    extract_started = time.perf_counter()
    audio_path = prepare_audio(input_path, work_dir)
    phase_timings = {
        "ffmpeg_extract_seconds": time.perf_counter() - extract_started,
        "process_started": process_started,
    }
    srt_path = output_dir / f"{input_path.stem}.{args.model}.srt"

    from pipeline_stages import TaskContext, transcribe_stage

    transcribe_stage(
        TaskContext(input_path.name, input_path, work_dir, output_dir),
        audio_path=audio_path,
        srt_path=srt_path,
        reuse_existing=False,
        config=SimpleNamespace(
            model=args.model, model_dir=model_dir, device=args.device,
            compute_type=args.compute_type, asr_mode=args.asr_mode,
            language=args.language, beam_size=args.beam_size,
            vad_filter=not args.no_vad, local_files_only=args.local_files_only,
            language_profile_id=args.language_profile, language_profile_name="",
            lang_profile_config=getattr(args, "profile_config", {}),
            quality_preset=getattr(args, "quality_preset", ""),
            word_timestamps=args.word_timestamps,
            resegment_subtitles=args.resegment_subtitles,
            asr_retry_mode=args.asr_retry_mode,
            asr_hotword_prompt=args.asr_hotword_prompt,
            profile_glossary=getattr(args, "profile_glossary", []),
            effective_asr_config=getattr(args, "effective_asr_config", {}),
            phase_timings=phase_timings,
        ),
    )

    print(f"Done: {srt_path}")

    # Translation stage
    if args.translate:
        _run_translation(args, srt_path, output_dir)
        mode_tag = "bilingual" if args.translation_mode == "bilingual" else "translated"
        translated_path = output_dir / f"{input_path.stem}.{args.model}.{mode_tag}.{args.target_language}.srt"
        print(f"Done: {translated_path}")

    if "ass" in subtitle_formats:
        print(f"ASS: {ASS_RESERVED_MESSAGE}")

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

    from subtitle_translate import build_effective_translation_prompt, translate_srt

    effective_prompt = build_effective_translation_prompt(
        style_prompt=getattr(args, "profile_translation_style", ""),
        custom_prompt=args.translation_prompt,
        glossary=getattr(args, "profile_glossary", []),
    )
    profile_quality = (
        getattr(args, "profile_config", {}).get("quality", {})
        if isinstance(getattr(args, "profile_config", {}), dict)
        else {}
    )

    translate_srt(
        input_path=srt_path,
        output_path=translated_path,
        api_provider=args.api_provider,
        api_base=args.api_base,
        api_key=api_key,
        llm_model=args.llm_model,
        translation_quality_model=getattr(args, "translation_quality_model", ""),
        target_language=args.target_language,
        batch_size=args.translation_batch_size,
        temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        system_prompt=effective_prompt,
        context_window=args.context_window,
        reliability_mode=args.translation_reliability_mode,
        max_extra_requests=args.translation_max_extra_requests,
        translation_strategy_mode=args.translation_strategy_mode,
        scene_gap_seconds=args.translation_scene_gap_seconds,
        max_cps_zh=float(
            getattr(args, "translation_max_cps_zh", None)
            or profile_quality.get("max_cps_zh", 8)
        ),
        max_chars_per_subtitle_zh=int(
            getattr(args, "translation_max_chars_per_subtitle_zh", None)
            or profile_quality.get("max_chars_per_subtitle_zh", 36)
        ),
        profile_glossary=getattr(args, "profile_glossary", []),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a video/audio file into an SRT subtitle file with faster-whisper."
    )
    parser.add_argument("input", help="Input video or audio file.")
    parser.add_argument("--model", default="small", help="Whisper model name. Example: tiny, base, small, medium, large-v3.")
    parser.add_argument("--device", default="auto", choices=["cpu", "cuda", "auto"], help="Run device. auto prefers CUDA and falls back to CPU.")
    parser.add_argument(
        "--compute-type",
        default=None,
        help="Compute type. CPU usually uses int8; CUDA usually uses float16.",
    )
    parser.add_argument(
        "--asr-mode",
        choices=["auto", "fixed", "multilingual"],
        default=None,
        help="ASR language mode. Omit for auto, or for legacy --language inference.",
    )
    parser.add_argument("--language", default=None, help="Source language code. Required by --asr-mode fixed.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated SRT files.")
    parser.add_argument("--model-dir", default="models", help="Directory for downloaded model files.")
    parser.add_argument("--work-dir", default="work", help="Directory for temporary extracted audio.")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size for decoding.")
    parser.add_argument("--no-vad", action="store_true", help="Disable voice activity detection.")
    parser.add_argument("--no-condition-on-previous-text", action="store_true", help="Disable conditioning on previous text during transcription.")
    parser.add_argument("--quality-preset", choices=["speed", "balanced", "quality"], default="", help="ASR quality loop preset.")
    parser.add_argument("--word-timestamps", dest="word_timestamps", action="store_true", default=None, help="Enable faster-whisper word timestamps.")
    parser.add_argument("--no-word-timestamps", dest="word_timestamps", action="store_false", help="Disable faster-whisper word timestamps.")
    parser.add_argument("--resegment-subtitles", dest="resegment_subtitles", action="store_true", default=None, help="Rebuild subtitle cues from word timestamps.")
    parser.add_argument("--no-resegment-subtitles", dest="resegment_subtitles", action="store_false", help="Disable deterministic subtitle resegmentation.")
    parser.add_argument("--asr-retry-mode", choices=["off", "dry_run", "apply"], default=None, help="Controlled local ASR retry mode.")
    parser.add_argument("--asr-hotword-prompt", default="", help="Short ASR prompt for fixed names or terms.")
    parser.add_argument("--local-files-only", action="store_true", help="Do not download models; only use local model files.")

    # Translation options
    parser.add_argument("--translate", action="store_true", help="Enable LLM translation after transcription.")
    parser.add_argument("--api-provider", default="openai-compatible",
                        choices=["openai-compatible", "anthropic"],
                        help="LLM API provider type.")
    parser.add_argument("--api-base", default="", help="LLM API base URL.")
    parser.add_argument("--api-key", default="", help="LLM API key.")
    parser.add_argument("--llm-model", default="", help="LLM model name.")
    parser.add_argument(
        "--translation-quality-model", default="",
        help="Optional model for preview repair candidates and judging.",
    )
    parser.add_argument("--target-language", default="zh-CN", help="Target language code.")
    parser.add_argument("--translation-batch-size", type=int, default=20, help="Translation batch size.")
    parser.add_argument("--translation-temperature", type=float, default=0.2, help="Translation temperature.")
    parser.add_argument("--translation-mode", default="bilingual",
                        choices=["bilingual", "translated"],
                        help="Translation output mode.")
    parser.add_argument("--context-window", type=int, default=3, help="Context window size for translation.")
    parser.add_argument("--translation-prompt", default="", help="Custom translation system prompt.")
    parser.add_argument("--translation-reliability-mode", choices=["off", "preview"], default=None, help="Translation recovery mode; default comes from Language Profile or off.")
    parser.add_argument("--translation-max-extra-requests", type=int, default=None, help="Shared preview recovery/repair request budget (0-50).")
    parser.add_argument(
        "--translation-strategy-mode",
        choices=[
            "standard",
            "three_pass",
            "semantic_review",
            "wenyi_review",
            "semantic_wenyi_review",
        ],
        default=None,
        help="Translation strategy.",
    )
    parser.add_argument("--translation-scene-gap-seconds", type=float, default=None, help="Scene split silence gap in seconds.")
    parser.add_argument("--translation-max-cps-zh", type=float, default=None, help="Optional Chinese CPS budget override for three-pass translation.")
    parser.add_argument("--translation-max-chars-per-subtitle-zh", type=int, default=None, help="Optional Chinese character budget override for three-pass translation.")
    parser.add_argument("--language-profile", default="", help="Language Profile ID for ASR and translation settings.")
    parser.add_argument("--subtitle-formats", default=None, help="Subtitle output formats. SRT is always enabled; ASS is reserved.")
    parser.add_argument("--ass-style-id", default=None, help="Reserved ASS style id. No .ass file is generated in this version.")
    args = parser.parse_args()
    args.profile_translation_style = ""
    args.profile_glossary = []
    args.profile_config = {}

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
                args.profile_config = profile
                asr = profile.get("asr", {})
                if not _explicit("--model") and asr.get("whisper_model"):
                    args.model = asr["whisper_model"]
                if not _explicit("--device") and asr.get("whisper_device"):
                    args.device = asr["whisper_device"]
                if not _explicit("--compute-type") and asr.get("compute_type"):
                    args.compute_type = asr["compute_type"]
                profile_mode = profile.get("asr_mode")
                profile_language = asr.get("language")
                if not profile_language:
                    source_language = str(profile.get("source_language") or "").strip()
                    if source_language and source_language != "auto":
                        profile_language = source_language
                if not _explicit("--asr-mode"):
                    args.asr_mode = profile_mode or (
                        "fixed" if profile_language else "auto"
                    )
                if (
                    not _explicit("--language")
                    and not (
                        _explicit("--asr-mode")
                        and args.asr_mode in {"auto", "multilingual"}
                    )
                    and profile_language
                ):
                    args.language = profile_language
                if not _explicit("--beam-size") and asr.get("beam_size") is not None:
                    args.beam_size = asr["beam_size"]
                if not _explicit("--no-vad") and asr.get("vad_filter") is False:
                    args.no_vad = True
                args.profile_translation_style = profile.get("translation_style", "")
                args.profile_glossary = profile.get("glossary", [])
                if not _explicit("--target-language") and profile.get("target_language"):
                    args.target_language = profile["target_language"]
                profile_reliability = profile.get("translation_reliability", {})
                if not _explicit("--translation-reliability-mode"):
                    args.translation_reliability_mode = profile_reliability.get("mode", "off")
                if not _explicit("--translation-max-extra-requests"):
                    args.translation_max_extra_requests = profile_reliability.get("max_extra_requests", 12)
                profile_translation_strategy = profile.get("translation_strategy", {})
                if not _explicit("--translation-strategy-mode"):
                    args.translation_strategy_mode = profile_translation_strategy.get("mode", "standard")
                if not _explicit("--translation-scene-gap-seconds"):
                    args.translation_scene_gap_seconds = profile_translation_strategy.get(
                        "scene_gap_seconds", 30.0
                    )
                subtitle_style = profile.get("subtitle_style", {})
                if isinstance(subtitle_style, dict):
                    if not _explicit("--subtitle-formats") and subtitle_style.get("formats"):
                        formats = subtitle_style.get("formats")
                        args.subtitle_formats = ",".join(formats) if isinstance(formats, list) else str(formats)
                    if not _explicit("--ass-style-id") and subtitle_style.get("ass_style_id"):
                        args.ass_style_id = subtitle_style["ass_style_id"]
            else:
                print(f"Warning: Language Profile '{profile_id}' not found, using CLI args", file=sys.stderr)
        except Exception as exc:
            print(f"Warning: cannot load Language Profile store: {exc}", file=sys.stderr)

    from translation_reliability import normalize_reliability_config
    from translation_strategy import normalize_translation_strategy

    try:
        reliability = normalize_reliability_config(
            args.translation_reliability_mode or "off",
            max_extra_requests=args.translation_max_extra_requests,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.translation_reliability_mode = reliability["mode"]
    args.translation_max_extra_requests = reliability["max_extra_requests"]
    try:
        translation_strategy = normalize_translation_strategy({
            "mode": args.translation_strategy_mode or "standard",
            "scene_gap_seconds": args.translation_scene_gap_seconds or 30.0,
        })
    except ValueError as exc:
        parser.error(str(exc))
    args.translation_strategy_mode = translation_strategy["mode"]
    args.translation_scene_gap_seconds = translation_strategy["scene_gap_seconds"]

    if _explicit("--language") and not _explicit("--asr-mode"):
        args.asr_mode = "fixed"
    try:
        explicit_loop = {}
        if _explicit("--word-timestamps", "--no-word-timestamps"):
            explicit_loop["word_timestamps"] = args.word_timestamps
        if _explicit("--resegment-subtitles", "--no-resegment-subtitles"):
            explicit_loop["resegment_subtitles"] = args.resegment_subtitles
        if _explicit("--asr-retry-mode"):
            explicit_loop["asr_retry_mode"] = args.asr_retry_mode
        if _explicit("--asr-hotword-prompt"):
            explicit_loop["asr_hotword_prompt"] = args.asr_hotword_prompt
        loop, loop_sources = resolve_quality_loop_config(
            explicit=explicit_loop,
            preset=args.quality_preset if _explicit("--quality-preset") else "",
            profile_asr=(
                args.profile_config.get("asr", {})
                if isinstance(args.profile_config, dict) else {}
            ),
        )
        if (
            "model" in loop
            and loop_sources.get("model", {}).get("source") == "quality_preset"
            and not _explicit("--model")
        ):
            args.model = str(loop["model"])
        args.quality_preset = str(loop.get("quality_preset") or "")
        args.word_timestamps = bool(loop.get("word_timestamps"))
        args.resegment_subtitles = bool(loop.get("resegment_subtitles"))
        args.asr_retry_mode = str(loop.get("asr_retry_mode") or "off")
        args.asr_hotword_prompt = str(loop.get("asr_hotword_prompt") or "")
        args.effective_asr_config = {
            **loop_sources,
            "model": {
                "value": args.model,
                "source": "explicit_request" if _explicit("--model") else (
                    "quality_preset"
                    if args.quality_preset == "quality" else (
                        "language_profile"
                        if isinstance(args.profile_config, dict)
                        and (args.profile_config.get("asr") or {}).get("whisper_model")
                        else "default"
                    )
                ),
            },
        }
        args.asr_mode, args.language = normalize_asr_request(
            args.asr_mode,
            args.language,
        )
    except ValueError as exc:
        parser.error(str(exc))
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
    result = run_text(command, capture_output=True)
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
    asr_mode: str = "auto",
    language: str | None = None,
    beam_size: int,
    vad_filter: bool,
    local_files_only: bool,
    language_profile_id: str = "",
    language_profile_name: str = "",
    condition_on_previous_text: bool = True,
    decode_options: AsrDecodeOptions | None = None,
    artifact_out: list[TranscriptionArtifact] | None = None,
    session: AsrSession | None = None,
    quality_preset: str = "",
    word_timestamps: bool = False,
    resegment_subtitles: bool = False,
    asr_retry_mode: str = "off",
    asr_hotword_prompt: str = "",
    profile_glossary: list[dict] | None = None,
    effective_asr_config: dict | None = None,
    phase_timings: dict[str, float] | None = None,
) -> dict | None:
    """Run the selected faster-whisper mode and atomically write SRT + diagnostics."""
    asr_mode, language = normalize_asr_request(asr_mode, language)
    options = decode_options or AsrDecodeOptions(
        condition_on_previous_text=condition_on_previous_text
    )
    options.validate()
    asr_retry_mode = normalize_asr_retry_mode(asr_retry_mode)
    initial_prompt = _bounded_prompt([asr_hotword_prompt], max_chars=512)
    function_started = time.perf_counter()
    timings = dict(phase_timings or {})
    model_load_started = time.perf_counter()
    reused_session = session is not None
    if session is None:
        session = create_asr_session(
            model_name=model_name, model_dir=model_dir, device=device,
            compute_type=compute_type, local_files_only=local_files_only,
        )
    elif session.model_name != model_name or session.local_files_only != local_files_only:
        raise ValueError("ASR session configuration does not match transcription request")
    timings["model_load_seconds"] = (
        0.0 if reused_session else time.perf_counter() - model_load_started
    )
    model = session.model
    device = session.device
    compute_type = session.compute_type
    backend_version = _package_version("faster-whisper")

    blocks: list[dict[str, Any]] = []
    initial_asr_started = time.perf_counter()
    if asr_mode == "multilingual":
        artifact, blocks = _transcribe_multilingual(
            model=model,
            audio_path=audio_path,
            beam_size=beam_size,
            options=options,
            backend_version=backend_version,
            word_timestamps=word_timestamps,
            initial_prompt=initial_prompt,
        )
        detected = "multilingual"
        probability = None
    else:
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
            initial_prompt=initial_prompt or None,
            **options.transcribe_kwargs(vad_filter),
        )
        detected = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)
        artifact_cues = _segments_to_cues(segments)
        artifact = TranscriptionArtifact(
            cues=tuple(artifact_cues),
            language=str(detected or ""),
            language_probability=round(probability, 4) if probability is not None else None,
            duration_seconds=float(getattr(info, "duration", 0.0) or 0.0) or None,
            backend_versions=(("faster-whisper", backend_version),),
        )
    timings["initial_asr_seconds"] = time.perf_counter() - initial_asr_started
    if not artifact.cues:
        raise RuntimeError("未检测到可转写语音。请确认视频包含清晰对白后重试。")

    original_artifact = artifact
    prob_str = f"{probability:.2f}" if probability is not None else "N/A"
    print(f"ASR mode: {asr_mode}")
    print(f"Detected language: {detected or 'unknown'} ({prob_str})")
    review_indexes = suspicious_cue_indexes(original_artifact.cues)
    retry_started = time.perf_counter()
    retry_report = _run_controlled_asr_retry(
        model=model,
        audio_path=audio_path,
        baseline=original_artifact,
        mode=asr_retry_mode,
        asr_mode=asr_mode,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        options=options,
        word_timestamps=word_timestamps,
        asr_hotword_prompt=asr_hotword_prompt,
        profile_glossary=profile_glossary or [],
    )
    timings["retry_seconds"] = time.perf_counter() - retry_started
    if asr_retry_mode == "apply" and retry_report["accepted_window_count"]:
        accepted_windows = [
            tuple(window["window"])
            for window in retry_report.get("windows", [])
            if window.get("accepted")
        ]
        retry_artifact = retry_report.pop("_retry_artifact", None)
        if isinstance(retry_artifact, TranscriptionArtifact):
            artifact = merge_retry_artifact(original_artifact, retry_artifact, accepted_windows)
    else:
        retry_report.pop("_retry_artifact", None)
    resegment_started = time.perf_counter()
    resegment_result = SubtitleResegmenter().resegment(artifact, enabled=resegment_subtitles)
    timings["resegment_seconds"] = time.perf_counter() - resegment_started
    artifact = resegment_result.artifact
    distinct_languages = sorted({
        str(block.get("language") or "") for block in blocks if block.get("language")
    })
    word_timing_count = sum(len(cue.words) for cue in artifact.cues)
    effective_config = effective_asr_config or {
        "model": {"value": model_name, "source": "default"},
        "quality_preset": {"value": quality_preset, "source": "explicit_request" if quality_preset else "default"},
        "word_timestamps": {"value": bool(word_timestamps), "source": "explicit_request" if word_timestamps else "default"},
        "resegment_subtitles": {"value": bool(resegment_subtitles), "source": "explicit_request" if resegment_subtitles else "default"},
        "asr_retry_mode": {"value": asr_retry_mode, "source": "explicit_request" if asr_retry_mode != "off" else "default"},
        "asr_hotword_prompt": {"value": bool(asr_hotword_prompt), "source": "explicit_request" if asr_hotword_prompt else "default"},
    }
    process_started = timings.pop("process_started", None)
    timings.setdefault("ffmpeg_extract_seconds", 0.0)
    timings["total_elapsed_seconds"] = (
        time.perf_counter() - float(process_started)
        if process_started is not None
        else timings["ffmpeg_extract_seconds"] + (time.perf_counter() - function_started)
    )
    phase_timing_report = {
        key: round(float(timings.get(key, 0.0)), 6)
        for key in (
            "ffmpeg_extract_seconds",
            "model_load_seconds",
            "initial_asr_seconds",
            "retry_seconds",
            "resegment_seconds",
            "total_elapsed_seconds",
        )
    }
    lang_info = {
        "report_schema_version": 2,
        "asr_mode": asr_mode,
        "source_language": detected or language or "",
        "language_probability": round(probability, 4) if probability is not None else None,
        "distinct_languages": distinct_languages,
        "block_count": len(blocks),
        "blocks": blocks,
        "manual_review_cue_indexes": [index + 1 for index in review_indexes],
        "manual_review_count": len(review_indexes),
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "language_profile": language_profile_id,
        "language_profile_name": language_profile_name,
        "forced_language": language,
        "vad_filter": vad_filter,
        "beam_size": beam_size,
        "condition_on_previous_text": options.condition_on_previous_text,
        "quality_preset": quality_preset,
        "word_timestamps": word_timestamps,
        "word_timing_count": word_timing_count,
        "resegment_summary": resegment_result.summary,
        "asr_retry": {
            "mode": asr_retry_mode,
            "recipe_version": ASR_RETRY_RECIPE_VERSION,
        },
        "asr_retry_report": _public_retry_report(retry_report),
        "effective_asr_config": effective_config,
        "backend_versions": dict(artifact.backend_versions),
        "local_files_only": local_files_only,
        "phase_timings": phase_timing_report,
    }

    review_payload = _build_asr_review(
        audio_path=audio_path,
        artifact=artifact,
        review_indexes=review_indexes,
        model_name=model_name,
        asr_mode=asr_mode,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        options=options,
        language_probability=probability,
        word_timing_count=word_timing_count,
        resegment_summary=resegment_result.summary,
        asr_retry_mode=asr_retry_mode,
        asr_retry_report=_public_retry_report(retry_report),
        effective_asr_config=effective_config,
        phase_timings=phase_timing_report,
    )
    report_root = srt_path.parent.parent if srt_path.parent.name == "source" else srt_path.parent
    review_report_path = report_root / "reports" / f"{srt_path.stem}.asr_review.json"
    review_report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_review_path = review_report_path.with_name(f"{review_report_path.name}.tmp")
    write_json(tmp_review_path, review_payload)
    _replace_output_file(tmp_review_path, review_report_path)
    lang_info["asr_review_report"] = str(review_report_path.resolve())
    lang_info["asr_review_summary"] = review_payload["summary"]

    tmp_srt_path = srt_path.with_name(f"{srt_path.name}.tmp")
    try:
        with tmp_srt_path.open("w", encoding="utf-8") as file:
            for index, cue in enumerate(artifact.cues, start=1):
                file.write(f"{index}\n")
                file.write(f"{format_srt_time(cue.start)} --> {format_srt_time(cue.end)}\n")
                file.write(f"{cue.text}\n\n")
        _replace_output_file(tmp_srt_path, srt_path)
    except Exception:
        try:
            tmp_srt_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    if artifact_out is not None:
        artifact_out.append(artifact)

    lang_json_path = srt_path.with_suffix(".lang.json")
    tmp_lang_json_path = lang_json_path.with_name(f"{lang_json_path.name}.tmp")
    write_json(tmp_lang_json_path, lang_info)
    _replace_output_file(tmp_lang_json_path, lang_json_path)
    print(f"Language detection saved: {lang_json_path}")

    return lang_info


def _build_asr_review(
    *,
    audio_path: Path,
    artifact: TranscriptionArtifact,
    review_indexes: list[int],
    model_name: str,
    asr_mode: str,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
    options: AsrDecodeOptions,
    language_probability: float | None,
    word_timing_count: int = 0,
    resegment_summary: dict | None = None,
    asr_retry_mode: str = "off",
    asr_retry_report: dict | None = None,
    effective_asr_config: dict | None = None,
    phase_timings: dict | None = None,
) -> dict:
    speech_intervals: list[tuple[float, float]] = []
    diagnostic_error = ""
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        audio = decode_audio(str(audio_path), sampling_rate=16000)
        spans = get_speech_timestamps(
            audio,
            VadOptions(
                threshold=options.vad_threshold,
                min_speech_duration_ms=0,
                max_speech_duration_s=60.0,
                min_silence_duration_ms=options.vad_min_silence_duration_ms,
                speech_pad_ms=options.vad_speech_pad_ms,
            ),
            sampling_rate=16000,
        )
        speech_intervals = [
            (float(row["start"]) / 16000, float(row["end"]) / 16000)
            for row in spans
            if int(row.get("end", 0)) > int(row.get("start", 0))
        ]
    except Exception as exc:
        diagnostic_error = str(exc)[:300]

    uncovered = uncovered_speech_intervals(speech_intervals, artifact.cues)
    suspicious = []
    for index in review_indexes:
        cue = artifact.cues[index]
        suspicious.append(
            {
                "cue_index": index + 1,
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "text": cue.text,
                "avg_logprob": cue.avg_logprob,
                "compression_ratio": cue.compression_ratio,
                "no_speech_prob": cue.no_speech_prob,
            }
        )
    warning = bool(uncovered or suspicious)
    candidate_summary = [
        {
            **item,
            "start_timecode": format_srt_time(float(item["start"])),
            "end_timecode": format_srt_time(float(item["end"])),
        }
        for item in uncovered
    ]
    return {
        "report_schema_version": 2,
        "schema_version": 2,
        "status": "warning" if warning else "pass",
        "word_timing_count": int(word_timing_count),
        "resegment_summary": resegment_summary or {
            "enabled": False,
            "applied": False,
            "fallback_reason": None,
            "input_cue_count": len(artifact.cues),
            "output_cue_count": len(artifact.cues),
            "word_timing_count": 0,
        },
        "asr_retry": {
            "mode": asr_retry_mode,
            "recipe_version": ASR_RETRY_RECIPE_VERSION,
        },
        "asr_retry_report": asr_retry_report or empty_retry_report(asr_retry_mode),
        "effective_asr_config": effective_asr_config or {},
        "phase_timings": phase_timings or {
            "ffmpeg_extract_seconds": 0.0,
            "model_load_seconds": 0.0,
            "initial_asr_seconds": 0.0,
            "retry_seconds": 0.0,
            "resegment_seconds": 0.0,
            "total_elapsed_seconds": 0.0,
        },
        "summary": {
            "status": "warning" if warning else "pass",
            "uncovered_speech_count": len(uncovered),
            "candidate_count": len(uncovered),
            "candidates": candidate_summary,
            "suspicious_cue_count": len(suspicious),
            "diagnostic_available": not bool(diagnostic_error),
            "message": _retry_summary_message(
                warning=warning,
                mode=asr_retry_mode,
                report=asr_retry_report or empty_retry_report(asr_retry_mode),
            ),
        },
        "uncovered_speech_intervals": uncovered,
        "suspicious_cues": suspicious,
        "language_probability": (
            round(float(language_probability), 4)
            if language_probability is not None
            else None
        ),
        "configuration": {
            "model": model_name,
            "asr_mode": asr_mode,
            "language": language,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "condition_on_previous_text": options.condition_on_previous_text,
            "word_timestamps": bool(word_timing_count),
            "cue_padding_seconds": 0.35,
            "minimum_uncovered_seconds": 1.0,
            "merge_gap_seconds": 0.5,
        },
        "diagnostic_error": diagnostic_error,
    }


def _retry_summary_message(*, warning: bool, mode: str, report: dict) -> str:
    accepted = int(report.get("accepted_window_count") or 0)
    executed = int(report.get("executed_window_count") or 0)
    if mode == "apply" and accepted:
        return f"ASR 复核已事务式接受 {accepted} 个局部重试窗口；请继续核对审计报告。"
    if mode == "apply":
        return "ASR 已执行受控局部重试，但没有候选通过替换门槛。"
    if mode == "dry_run" and executed:
        return f"ASR 已 dry-run 评估 {executed} 个局部重试窗口，未改写输出。"
    if mode == "dry_run":
        return "ASR retry 为 dry-run，未规划可执行窗口，未改写输出。"
    if warning:
        return "发现可能漏识别或低可信区间，仅供人工复核；ASR retry 已关闭。"
    return "未发现需要人工关注的 ASR 区间；ASR retry 已关闭。"


def _transcribe_multilingual(
    *,
    model: Any,
    audio_path: Path,
    beam_size: int,
    options: AsrDecodeOptions,
    backend_version: str,
    word_timestamps: bool = False,
    initial_prompt: str = "",
) -> tuple[TranscriptionArtifact, list[dict[str, Any]]]:
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(audio_path), sampling_rate=16000)
    vad_options = VadOptions(
        threshold=options.vad_threshold,
        min_speech_duration_ms=0,
        max_speech_duration_s=60.0,
        min_silence_duration_ms=options.vad_min_silence_duration_ms,
        speech_pad_ms=options.vad_speech_pad_ms,
    )
    speech_spans = get_speech_timestamps(audio, vad_options, sampling_rate=16000)
    planned_blocks = plan_vad_blocks(
        speech_spans,
        audio_samples=len(audio),
        sampling_rate=16000,
    )
    if not planned_blocks:
        raise RuntimeError("未检测到可转写语音。请确认视频包含清晰对白后重试。")

    all_cues: list[TranscriptionCue] = []
    reports: list[dict[str, Any]] = []
    for index, block in enumerate(planned_blocks, start=1):
        chunk = audio[block.start_sample:block.end_sample]
        try:
            segments, info = model.transcribe(
                chunk,
                language=None,
                beam_size=beam_size,
                vad_filter=False,
                word_timestamps=word_timestamps,
                initial_prompt=initial_prompt or None,
                **options.transcribe_kwargs(False),
            )
            local_cues = _segments_to_cues(segments, offset=block.start)
            if not local_cues:
                raise RuntimeError("该语音块未生成有效字幕")
        except Exception as exc:
            raise RuntimeError(
                f"多语言转写在第 {index} 个语音块失败：{exc}"
            ) from exc
        language = str(getattr(info, "language", "") or "")
        probability = getattr(info, "language_probability", None)
        suspicious = suspicious_cue_indexes(local_cues)
        review_recommended = (
            probability is not None and float(probability) < 0.70
        ) or bool(suspicious)
        reports.append({
            "index": index,
            "start": round(block.start, 3),
            "end": round(block.end, 3),
            "speech_seconds": round(block.speech_seconds, 3),
            "language": language,
            "language_probability": (
                round(float(probability), 4) if probability is not None else None
            ),
            "cue_count": len(local_cues),
            "review_recommended": review_recommended,
        })
        all_cues.extend(local_cues)
        print(
            f"Multilingual block {index}/{len(planned_blocks)}: "
            f"{block.start:.2f}-{block.end:.2f}s, language={language or 'unknown'}, "
            f"cues={len(local_cues)}, review={review_recommended}"
        )

    merged_cues, removed = deduplicate_boundary_cues(all_cues)
    if removed:
        print(f"Multilingual boundary duplicates removed: {removed}")
    artifact = TranscriptionArtifact(
        cues=merged_cues,
        language="multilingual",
        duration_seconds=len(audio) / 16000,
        backend_versions=(("faster-whisper", backend_version),),
        metadata={"block_count": len(reports), "duplicates_removed": removed},
    )
    return artifact, reports


def _segments_to_cues(segments: Any, *, offset: float = 0.0) -> list[TranscriptionCue]:
    cues: list[TranscriptionCue] = []
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        start = max(0.0, float(getattr(segment, "start", 0.0)) + offset)
        end = max(start + 0.001, float(getattr(segment, "end", start)) + offset)
        words = []
        for word in getattr(segment, "words", None) or []:
            word_text = str(getattr(word, "word", None) or getattr(word, "text", "") or "")
            word_start = getattr(word, "start", None)
            word_end = getattr(word, "end", None)
            words.append(TranscriptionWord(
                start=(float(word_start) + offset) if word_start is not None else None,
                end=(float(word_end) + offset) if word_end is not None else None,
                text=word_text,
                probability=getattr(word, "probability", None),
            ))
        cues.append(
            TranscriptionCue(
                start=start,
                end=end,
                text=text,
                words=tuple(words),
                avg_logprob=getattr(segment, "avg_logprob", None),
                compression_ratio=getattr(segment, "compression_ratio", None),
                no_speech_prob=getattr(segment, "no_speech_prob", None),
            )
        )
    return cues


def _run_controlled_asr_retry(
    *,
    model: Any,
    audio_path: Path,
    baseline: TranscriptionArtifact,
    mode: str,
    asr_mode: str,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
    options: AsrDecodeOptions,
    word_timestamps: bool,
    asr_hotword_prompt: str,
    profile_glossary: list[dict],
) -> dict:
    if mode == "off":
        return empty_retry_report(mode)
    windows, skipped = plan_retry_windows(baseline)
    if not windows and not skipped:
        return empty_retry_report(mode)
    retry_cues: list[TranscriptionCue] = []
    reports = list(skipped)
    retry_options = AsrDecodeOptions(
        condition_on_previous_text=False,
        repetition_penalty=1.05,
        no_repeat_ngram_size=3,
        vad_threshold=0.4,
        vad_min_silence_duration_ms=500,
        vad_speech_pad_ms=options.vad_speech_pad_ms,
    )
    for window in windows:
        prompt = _bounded_prompt([
            asr_hotword_prompt,
            _glossary_prompt_for_window(baseline, window, profile_glossary),
        ])
        try:
            segments, _info = model.transcribe(
                str(audio_path),
                language=language if asr_mode == "fixed" else None,
                beam_size=beam_size,
                vad_filter=vad_filter,
                word_timestamps=word_timestamps,
                initial_prompt=prompt or None,
                clip_timestamps=f"{window[0]:.3f},{window[1]:.3f}",
                **retry_options.transcribe_kwargs(vad_filter),
            )
            local_cues = _segments_to_cues(segments)
            local_cues = _normalize_retry_cue_offsets(local_cues, window)
            retry_cues.extend(local_cues)
            candidate = TranscriptionArtifact(
                cues=tuple(sorted((*retry_cues,), key=lambda cue: (cue.start, cue.end, cue.text))),
                language=baseline.language,
                language_probability=baseline.language_probability,
                duration_seconds=baseline.duration_seconds,
                backend_versions=baseline.backend_versions,
            )
            reports.append(select_retry_window(baseline, candidate, window))
        except Exception:
            reports.append(select_retry_window(
                baseline,
                TranscriptionArtifact(cues=(), duration_seconds=baseline.duration_seconds),
                window,
            ))
    retry_artifact = TranscriptionArtifact(
        cues=tuple(sorted(retry_cues, key=lambda cue: (cue.start, cue.end, cue.text))),
        language=baseline.language,
        language_probability=baseline.language_probability,
        duration_seconds=baseline.duration_seconds,
        backend_versions=baseline.backend_versions,
    )
    report = build_retry_report(mode, reports)
    report["_retry_artifact"] = retry_artifact
    if mode != "apply":
        report["accepted_window_count"] = 0
        for window_report in report["windows"]:
            window_report["accepted"] = False
            if "dry_run" not in window_report["reasons"]:
                window_report["reasons"] = list(window_report["reasons"]) + ["dry_run"]
    return report


def _normalize_retry_cue_offsets(cues: list[TranscriptionCue], window: tuple[float, float]) -> list[TranscriptionCue]:
    if not cues:
        return []
    start, end = window
    duration = end - start
    max_end = max(cue.end for cue in cues)
    min_start = min(cue.start for cue in cues)
    offset = start if max_end <= duration + 2.0 and min_start < max(0.5, start - 0.1) else 0.0
    normalized: list[TranscriptionCue] = []
    for cue in cues:
        shifted_words = tuple(
            TranscriptionWord(
                start=(word.start + offset) if word.start is not None else None,
                end=(word.end + offset) if word.end is not None else None,
                text=word.text,
                probability=word.probability,
            )
            for word in cue.words
        )
        shifted = TranscriptionCue(
            start=max(0.0, cue.start + offset),
            end=max(cue.start + offset + 0.001, cue.end + offset),
            text=cue.text,
            words=shifted_words,
            avg_logprob=cue.avg_logprob,
            compression_ratio=cue.compression_ratio,
            no_speech_prob=cue.no_speech_prob,
        )
        if shifted.start < end and shifted.end > start:
            normalized.append(shifted)
    return normalized


def _glossary_prompt_for_window(
    artifact: TranscriptionArtifact,
    window: tuple[float, float],
    glossary: list[dict],
) -> str:
    if not glossary:
        return ""
    indexes = [
        index for index, cue in enumerate(artifact.cues)
        if cue.start < window[1] and cue.end > window[0]
    ]
    expanded = set()
    for index in indexes:
        for nearby in range(max(0, index - 2), min(len(artifact.cues), index + 3)):
            expanded.add(nearby)
    text = " ".join(artifact.cues[index].text for index in sorted(expanded)).casefold()
    terms: list[str] = []
    seen: set[str] = set()
    for row in glossary:
        if not isinstance(row, dict):
            continue
        candidates: list[str] = []
        for key in ("source",):
            value = str(row.get(key) or "").strip()
            if value:
                candidates.append(value)
        for key in ("aliases", "asr_variants"):
            values = row.get(key)
            if isinstance(values, list):
                candidates.extend(str(item).strip() for item in values if str(item).strip())
        if not any(value.casefold() in text for value in candidates):
            continue
        for value in candidates:
            normalized = value.casefold()
            if normalized and normalized not in seen:
                seen.add(normalized)
                terms.append(value)
            if len(terms) >= 32:
                return _bounded_prompt(terms)
    return _bounded_prompt(terms)


def _bounded_prompt(parts: list[str], *, max_chars: int = 512) -> str:
    values = []
    seen: set[str] = set()
    for part in parts:
        for item in str(part or "").replace("\n", ",").split(","):
            value = item.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                values.append(value)
    text = ", ".join(values)
    return text[:max_chars]


def _public_retry_report(report: dict) -> dict:
    cleaned = dict(report)
    cleaned.pop("_retry_artifact", None)
    return cleaned


def _package_version(name: str) -> str:
    try:
        return package_version(name)
    except Exception:
        return "unknown"


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
