from __future__ import annotations

import argparse
import os
import sys
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
from asr_runtime import (
    AsrDecodeOptions,
    TranscriptionArtifact,
    TranscriptionCue,
    deduplicate_boundary_cues,
    normalize_asr_request,
    plan_vad_blocks,
    suspicious_cue_indexes,
)
from subtitle_model import (
    ASS_RESERVED_MESSAGE,
    DEFAULT_ASS_STYLE_ID,
    normalize_subtitle_formats,
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


def _resolve_local_model_source(model_name: str, model_dir: Path) -> str:
    requested = Path(model_name).expanduser()
    if requested.is_dir():
        return str(requested.resolve())
    if "/" in model_name:
        cache_name = f"models--{model_name.replace('/', '--')}"
    else:
        cache_name = f"models--Systran--faster-whisper-{model_name}"
    candidate = model_dir / cache_name
    if (candidate / "config.json").is_file() and (candidate / "model.bin").is_file():
        return str(candidate.resolve())
    return model_name


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
            compute_type=args.compute_type, asr_mode=args.asr_mode,
            language=args.language, beam_size=args.beam_size,
            vad_filter=not args.no_vad, local_files_only=args.local_files_only,
            language_profile_id=args.language_profile, language_profile_name="",
            lang_profile_config=getattr(args, "profile_config", {}),
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
) -> dict | None:
    """Run the selected faster-whisper mode and atomically write SRT + diagnostics."""
    asr_mode, language = normalize_asr_request(asr_mode, language)
    options = decode_options or AsrDecodeOptions(
        condition_on_previous_text=condition_on_previous_text
    )
    options.validate()
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
    backend_version = _package_version("faster-whisper")

    blocks: list[dict[str, Any]] = []
    if asr_mode == "multilingual":
        artifact, blocks = _transcribe_multilingual(
            model=model,
            audio_path=audio_path,
            beam_size=beam_size,
            options=options,
            backend_version=backend_version,
        )
        detected = "multilingual"
        probability = None
    else:
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
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
    if not artifact.cues:
        raise RuntimeError("未检测到可转写语音。请确认视频包含清晰对白后重试。")

    prob_str = f"{probability:.2f}" if probability is not None else "N/A"
    print(f"ASR mode: {asr_mode}")
    print(f"Detected language: {detected or 'unknown'} ({prob_str})")
    review_indexes = suspicious_cue_indexes(artifact.cues)
    distinct_languages = sorted({
        str(block.get("language") or "") for block in blocks if block.get("language")
    })
    lang_info = {
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
        "backend_versions": dict(artifact.backend_versions),
        "local_files_only": local_files_only,
    }

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


def _transcribe_multilingual(
    *,
    model: Any,
    audio_path: Path,
    beam_size: int,
    options: AsrDecodeOptions,
    backend_version: str,
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
        cues.append(
            TranscriptionCue(
                start=start,
                end=end,
                text=text,
                avg_logprob=getattr(segment, "avg_logprob", None),
                compression_ratio=getattr(segment, "compression_ratio", None),
                no_speech_prob=getattr(segment, "no_speech_prob", None),
            )
        )
    return cues


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
