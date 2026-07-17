from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
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
from asr_strategy import AsrDecodeOptions, TranscriptionArtifact, TranscriptionCue
from subtitle_model import (
    ASS_RESERVED_MESSAGE,
    DEFAULT_ASS_STYLE_ID,
    normalize_subtitle_formats,
)
from segment_asr_routing_integration import (
    DEFAULT_APPLY_WINDOW_SECONDS,
    DEFAULT_MAX_APPLY_WINDOWS,
    SegmentAsrRoutingError,
    SegmentAsrRoutingOptions,
    ensure_apply_is_not_strict,
    routing_user_message,
    run_segment_asr_routing,
    validate_options as validate_segment_routing_options,
)

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
    print(f"Loading model: {model_name}")
    print(f"Model dir: {model_dir}")
    print(f"Device: {resolved_device}, compute_type: {resolved_compute_type}")
    print(f"Local files only: {local_files_only}")
    try:
        model = WhisperModel(
            model_name, device=resolved_device, compute_type=resolved_compute_type,
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
    args = parse_args()
    segment_routing = SegmentAsrRoutingOptions(
        mode=args.segment_asr_routing,
        confidence_threshold=args.segment_routing_confidence_threshold,
        min_segments=args.segment_routing_min_segments,
        strict=args.segment_routing_strict,
        window_seconds=args.segment_routing_window_seconds,
        max_windows=args.segment_routing_max_windows,
        allow_large_run=args.segment_routing_allow_large_run,
    )
    try:
        ensure_apply_is_not_strict(segment_routing)
    except SegmentAsrRoutingError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

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

    audio_path = prepare_audio(input_path, work_dir)
    srt_path = output_dir / f"{input_path.stem}.{args.model}.srt"

    from pipeline_stages import TaskContext, transcribe_stage

    transcribe_stage(
        TaskContext(input_path.name, input_path, work_dir, output_dir),
        audio_path=audio_path,
        srt_path=srt_path,
        reuse_existing=False,
        config=SimpleNamespace(
            model=args.model, model_dir=model_dir, device=args.device,
            compute_type=args.compute_type, language=args.language, beam_size=args.beam_size,
            vad_filter=not args.no_vad, local_files_only=args.local_files_only,
            language_profile_id=args.language_profile, language_profile_name="",
            lang_profile_config=getattr(args, "profile_config", {}),
            asr_experiment_mode=args.asr_experiment_mode,
            asr_candidate_id=args.asr_candidate_id,
        ),
    )

    if segment_routing.mode != "off":
        try:
            result = run_segment_asr_routing(
                options=segment_routing,
                media_path=input_path,
                routing_input_path=audio_path,
                report_root=output_dir / "reports",
                model_name=args.model,
                device=args.device,
                compute_type=args.compute_type,
                local_files_only=args.local_files_only,
                normal_srt_path=srt_path,
                routed_srt_path=srt_path,
            )
        except SegmentAsrRoutingError as exc:
            message = routing_user_message(user_status="failed", failure_reason=str(exc))
            raise SystemExit(f"ERROR: {message}") from exc
        if result.message:
            print(result.message)
        if result.report_path:
            print(f"Segment ASR routing report: {result.report_path}")

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

    translate_srt(
        input_path=srt_path,
        output_path=translated_path,
        api_provider=args.api_provider,
        api_base=args.api_base,
        api_key=api_key,
        llm_model=args.llm_model,
        translation_quality_model=(
            getattr(args, "translation_quality_model", "") or args.llm_model
        ),
        target_language=args.target_language,
        batch_size=args.translation_batch_size,
        temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        system_prompt=effective_prompt,
        context_window=args.context_window,
        reliability_mode=args.translation_reliability_mode,
        max_extra_requests=args.translation_max_extra_requests,
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
    parser.add_argument("--language", default=None, help="Source language code, for example en, ja, ko, zh. Omit for auto detect.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated SRT files.")
    parser.add_argument("--model-dir", default="models", help="Directory for downloaded model files.")
    parser.add_argument("--work-dir", default="work", help="Directory for temporary extracted audio.")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size for decoding.")
    parser.add_argument("--no-vad", action="store_true", help="Disable voice activity detection.")
    parser.add_argument("--no-condition-on-previous-text", action="store_true", help="Disable conditioning on previous text during transcription.")
    parser.add_argument("--local-files-only", action="store_true", help="Do not download models; only use local model files.")
    parser.add_argument("--asr-experiment-mode", choices=["off", "dry_run", "apply"], default=None, help="ASR candidate mode; default comes from Language Profile or off.")
    parser.add_argument("--asr-candidate-id", default=None, help="Registered ASR candidate id.")

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
    parser.add_argument("--language-profile", default="", help="Language Profile ID for ASR and translation settings.")
    parser.add_argument("--subtitle-formats", default=None, help="Subtitle output formats. SRT is always enabled; ASS is reserved.")
    parser.add_argument("--ass-style-id", default=None, help="Reserved ASS style id. No .ass file is generated in this version.")
    parser.add_argument(
        "--segment-asr-routing",
        default="off",
        choices=["off", "dry_run", "apply"],
        help="Experimental segment ASR routing mode. Defaults to off.",
    )
    parser.add_argument(
        "--segment-routing-confidence-threshold",
        type=float,
        default=0.70,
        help="Confidence threshold for segment routing dry-run analysis.",
    )
    parser.add_argument(
        "--segment-routing-min-segments",
        type=int,
        default=1,
        help="Minimum segment count for usable segment routing evidence.",
    )
    parser.add_argument(
        "--segment-routing-strict",
        action="store_true",
        help="Fail instead of falling back when experimental segment routing fails.",
    )
    parser.add_argument(
        "--segment-routing-window-seconds",
        type=float,
        default=DEFAULT_APPLY_WINDOW_SECONDS,
        help="Apply-only full-coverage routing window length in seconds.",
    )
    parser.add_argument(
        "--segment-routing-max-windows",
        type=int,
        default=DEFAULT_MAX_APPLY_WINDOWS,
        help="Apply-only maximum routed windows before fallback or strict failure.",
    )
    parser.add_argument(
        "--segment-routing-allow-large-run",
        action="store_true",
        help="Allow apply to exceed --segment-routing-max-windows.",
    )

    args = parser.parse_args()
    try:
        validate_segment_routing_options(
            SegmentAsrRoutingOptions(
                mode=args.segment_asr_routing,
                confidence_threshold=args.segment_routing_confidence_threshold,
                min_segments=args.segment_routing_min_segments,
                strict=args.segment_routing_strict,
                window_seconds=args.segment_routing_window_seconds,
                max_windows=args.segment_routing_max_windows,
                allow_large_run=args.segment_routing_allow_large_run,
            )
        )
    except SegmentAsrRoutingError as exc:
        parser.error(str(exc))
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
                if not _explicit("--language") and asr.get("language"):
                    args.language = asr["language"]
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
                subtitle_style = profile.get("subtitle_style", {})
                if isinstance(subtitle_style, dict):
                    if not _explicit("--subtitle-formats") and subtitle_style.get("formats"):
                        formats = subtitle_style.get("formats")
                        args.subtitle_formats = ",".join(formats) if isinstance(formats, list) else str(formats)
                    if not _explicit("--ass-style-id") and subtitle_style.get("ass_style_id"):
                        args.ass_style_id = subtitle_style["ass_style_id"]
                profile_strategy = profile.get("asr_strategy", {})
                if not _explicit("--asr-experiment-mode"):
                    args.asr_experiment_mode = profile_strategy.get("mode", "off")
                if not _explicit("--asr-candidate-id"):
                    args.asr_candidate_id = profile_strategy.get("candidate_id", "")
            else:
                print(f"Warning: Language Profile '{profile_id}' not found, using CLI args", file=sys.stderr)
        except Exception as exc:
            print(f"Warning: cannot load Language Profile store: {exc}", file=sys.stderr)

    from asr_strategy import validate_strategy_config
    from translation_reliability import normalize_reliability_config

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
        strategy = validate_strategy_config(
            {"mode": args.asr_experiment_mode or "off", "candidate_id": args.asr_candidate_id or ""},
            model=args.model,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.asr_experiment_mode = strategy["mode"]
    args.asr_candidate_id = strategy["candidate_id"]
    if args.asr_candidate_id == "mixed-route-v1" and args.asr_experiment_mode == "dry_run":
        args.segment_asr_routing = "dry_run"
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
    language: str | None,
    beam_size: int,
    vad_filter: bool,
    local_files_only: bool,
    language_profile_id: str = "",
    language_profile_name: str = "",
    condition_on_previous_text: bool = True,
    decode_options: AsrDecodeOptions | None = None,
    artifact_out: list[TranscriptionArtifact] | None = None,
    session: AsrSession | None = None,
) -> dict | None:
    """Run Whisper transcription and write SRT.

    Returns language detection info dict (or None on failure):
        {"source_language": "ja", "language_probability": 0.94, "model": "large-v3"}
    """
    if session is None:
        session = create_asr_session(
            model_name=model_name, model_dir=model_dir, device=device,
            compute_type=compute_type, local_files_only=local_files_only,
        )
    elif session.model_name != model_name or session.local_files_only != local_files_only:
        raise ValueError("ASR session configuration does not match transcription request")
    model = session.model
    device = session.device
    compute_type = session.compute_type
    if language is not None:
        language = language.strip() or None
        if language == "auto":
            language = None

    options = decode_options or AsrDecodeOptions(
        condition_on_previous_text=condition_on_previous_text
    )
    options.validate()
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        **options.transcribe_kwargs(vad_filter),
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
            "condition_on_previous_text": options.condition_on_previous_text,
        }

    tmp_srt_path = srt_path.with_name(f"{srt_path.name}.tmp")
    artifact_cues: list[TranscriptionCue] = []
    try:
        with tmp_srt_path.open("w", encoding="utf-8") as file:
            for index, segment in enumerate(segments, start=1):
                text = segment.text.strip()
                if not text:
                    continue

                artifact_cues.append(
                    TranscriptionCue(
                        start=float(segment.start),
                        end=float(segment.end),
                        text=text,
                        avg_logprob=getattr(segment, "avg_logprob", None),
                        compression_ratio=getattr(segment, "compression_ratio", None),
                        no_speech_prob=getattr(segment, "no_speech_prob", None),
                    )
                )

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

    if artifact_out is not None:
        artifact_out.append(
            TranscriptionArtifact(
                cues=tuple(artifact_cues),
                language=str(detected or ""),
                language_probability=round(probability, 4) if probability is not None else None,
                duration_seconds=float(getattr(info, "duration", 0.0) or 0.0) or None,
            )
        )

    if lang_info is not None:
        lang_json_path = srt_path.with_suffix(".lang.json")
        tmp_lang_json_path = lang_json_path.with_name(f"{lang_json_path.name}.tmp")
        write_json(tmp_lang_json_path, lang_info)
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
