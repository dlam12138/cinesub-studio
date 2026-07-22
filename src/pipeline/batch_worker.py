"""
Batch Subtitle Pipeline Worker.

Turns input media into source, translated, and bilingual subtitle outputs:

    input/ -> discover -> extract audio -> Whisper transcribe -> LLM translate -> quality check -> output

Example:
    .\\.venv\\Scripts\\python.exe -B src\\pipeline\\batch_worker.py --input input --model large-v3 --device cuda

Stages:
    1. Scan input media files.
    2. Extract audio and transcribe with faster-whisper.
    3. Translate SRT through the configured LLM provider.
    4. Run subtitle format and translation quality checks.
    5. Write subtitles, reports, and task state files.
"""

from __future__ import annotations

import json
import hashlib
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path


# Ensure src subdirectories are on sys.path for cross-module imports when run directly
_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "pipeline", "config", "web", "tools"):
    _subpath = str(_src / _sub)
    if _subpath not in sys.path:
        sys.path.insert(0, _subpath)

from encoding_utils import read_json
from output_paths import pipeline_output_dirs, plan_pipeline_outputs
from pipeline_stages import (
    StageError,
    TaskContext,
    archive_stage,
    extract_audio_stage,
    quality_check_stage,
    transcribe_stage,
    translate_stage,
)
from pipeline_cli import build_pipeline_parser
from pipeline_config import resolve_cli_config
from pipeline_reporting import safe_console_print, show_review, show_review_detail, show_status
from runtime_paths import resolve_runtime_paths
from stage_event_log import write_stage_event
from task_state import (
    RetryPlan,
    TaskStage,
    TaskState,
    completed_outputs_valid as validate_completed_outputs,
    is_valid_output_file as _is_valid_output_file,
    prepare_retry_failed_tasks,
    required_final_outputs as plan_required_final_outputs,
    set_state_root_provider,
)
from subtitle_model import (
    ASS_RESERVED_MESSAGE,
    DEFAULT_ASS_STYLE_ID,
    normalize_subtitle_formats,
)
from asr_runtime import ASR_RETRY_RECIPE_VERSION

PATHS = resolve_runtime_paths(Path(__file__).resolve())
PROJECT_ROOT = PATHS.project_root
SRC_ROOT = PATHS.src_root


DIR_INPUT = PROJECT_ROOT / "input"
DIR_WORK = PROJECT_ROOT / "work"
DIR_WORK_STATES = PROJECT_ROOT / "work" / "states"
DIR_OUTPUT = PROJECT_ROOT / "output"
DIR_ARCHIVE = PROJECT_ROOT / "archive"
DIR_FAILED = PROJECT_ROOT / "failed"
DIR_MODELS = PROJECT_ROOT / "models"
STAGE_EVENT_LOG = PATHS.logs_dir / "pipeline.events.jsonl"
set_state_root_provider(lambda: DIR_WORK_STATES)


VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".wav",
}


def discover_videos(input_dir: Path) -> list[Path]:
    """Return supported media files under the input directory."""
    if not input_dir.exists():
        return []

    videos: list[Path] = []
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(path)
        elif path.is_dir():
            for subpath in sorted(path.rglob("*")):
                if subpath.is_file() and subpath.suffix.lower() in VIDEO_EXTENSIONS:
                    videos.append(subpath)

    return videos



@dataclass
class BatchConfig:
    """Batch processing configuration."""
    input_dir: Path = DIR_INPUT
    output_dir: Path = PROJECT_ROOT / "output"
    model_dir: Path = DIR_MODELS
    work_dir: Path = DIR_WORK

    model: str = "large-v3"
    device: str = "auto"
    compute_type: str | None = None
    asr_mode: str = "auto"
    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = True
    local_files_only: bool = False
    quality_preset: str = ""
    word_timestamps: bool = False
    resegment_subtitles: bool = False
    asr_retry_mode: str = "off"
    asr_hotword_prompt: str = ""
    effective_asr_config: dict | None = None

    translate: bool = True
    api_provider: str = "openai-compatible"
    api_base: str = ""
    api_key: str = ""
    llm_model: str = ""
    translation_quality_model: str = ""
    target_language: str = "zh-CN"
    translation_batch_size: int = 20
    translation_temperature: float = 0.2
    translation_mode: str = "bilingual"
    context_window: int = 3
    translation_prompt: str = ""
    translation_reliability_mode: str = "off"
    translation_max_extra_requests: int = 12
    translation_strategy_mode: str = "standard"
    translation_scene_gap_seconds: float = 30.0
    subtitle_formats: list[str] = field(default_factory=lambda: ["srt"])
    ass_style_id: str = DEFAULT_ASS_STYLE_ID
    subtitle_style: dict | None = None
    language_profile_id: str = ""
    language_profile_name: str = ""
    lang_profile_config: dict | None = None

    max_retries: int = 3
    skip_completed: bool = True
    move_completed: bool = True

    def __post_init__(self):
        from asr_runtime import normalize_asr_request
        from translation_strategy import normalize_translation_strategy

        self.asr_mode, self.language = normalize_asr_request(
            self.asr_mode,
            self.language,
        )
        strategy = normalize_translation_strategy({
            "mode": self.translation_strategy_mode,
            "scene_gap_seconds": self.translation_scene_gap_seconds,
        })
        self.translation_strategy_mode = strategy["mode"]
        self.translation_scene_gap_seconds = strategy["scene_gap_seconds"]
        if (
            self.translate
            and self.translation_strategy_mode in {
                "wenyi_review", "semantic_wenyi_review"
            }
            and not str(self.translation_quality_model or "").strip()
        ):
            raise ValueError(
                f"{self.translation_strategy_mode} requires "
                "translation_quality_model; Pro stages do not fall back to "
                "the Flash model"
            )
        if not self.api_key:
            self.api_key = os.environ.get("SUBTITLE_LLM_API_KEY", "")

    def asr_signature(self) -> str:
        payload = {
            "schema": 2,
            "mode": self.asr_mode,
            "language": self.language,
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "quality_preset": self.quality_preset,
            "word_timestamps": self.word_timestamps,
            "resegment_subtitles": self.resegment_subtitles,
            "asr_retry_mode": self.asr_retry_mode,
            "asr_retry_recipe_version": ASR_RETRY_RECIPE_VERSION,
            "asr_hotword_prompt": self.asr_hotword_prompt,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()



class BatchPipeline:
    """Batch subtitle production pipeline."""

    def __init__(self, config: BatchConfig):
        self.config = config
        self.tasks: list[TaskState] = []
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create required runtime directories."""
        for d in [
            self.config.input_dir,
            self.config.work_dir,
            DIR_WORK_STATES,
            *pipeline_output_dirs(self.config.output_dir),
            DIR_ARCHIVE,
            DIR_FAILED,
            self.config.model_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def _context(self, input_path: Path) -> TaskContext:
        return TaskContext(
            task_id=input_path.name,
            input_path=input_path,
            work_dir=self.config.work_dir,
            output_dir=self.config.output_dir,
        )

    def _record_stage_result(self, context: TaskContext, result) -> None:
        summary = ", ".join(path.name for path in result.outputs)
        reliability = result.data.get("translation_reliability") if result.data else None
        if reliability:
            summary = f"{summary}; translation_reliability={json.dumps(reliability, sort_keys=True)}"
        write_stage_event(
            STAGE_EVENT_LOG,
            task_id=context.task_id,
            stage=result.stage,
            event="reused" if result.reused else "completed",
            status=result.status,
            duration_seconds=result.duration_seconds,
            summary=summary,
        )

    def _record_stage_started(self, context: TaskContext, stage: str) -> None:
        write_stage_event(
            STAGE_EVENT_LOG,
            task_id=context.task_id,
            stage=stage,
            event="started",
            status="running",
        )

    def _record_stage_error(self, context: TaskContext, stage: str, exc: BaseException) -> None:
        write_stage_event(
            STAGE_EVENT_LOG,
            task_id=context.task_id,
            stage=stage,
            event="failed",
            status="failed",
            returncode=getattr(exc, "returncode", None),
            error_category=type(exc).__name__,
            summary=str(exc),
        )

    def scan(self) -> list[TaskState]:
        """Scan the input directory and return pending tasks."""
        videos = discover_videos(self.config.input_dir)
        tasks: list[TaskState] = []

        for video_path in videos:
            stem = video_path.stem
            state_path = DIR_WORK_STATES / f"{stem}.state.json"

            existing = TaskState.load(state_path)
            if existing and existing.status == "completed" and self.config.skip_completed:
                if self.completed_outputs_valid(existing):
                    print(f"  [skip] already completed with valid outputs: {video_path.name}")
                    continue
                print(f"  [warn] completed state has missing or empty final outputs: {video_path.name}")

            if existing:
                task = existing
                task.max_retries = self.config.max_retries
            else:
                task = TaskState(
                    file=video_path.name,
                    input_path=str(video_path.resolve()),
                    created_at=time.time(),
                    max_retries=self.config.max_retries,
                )

            task.asr_mode = self.config.asr_mode
            task.language = self.config.language or ""
            task.save()
            tasks.append(task)

        return tasks

    def run(self) -> dict:
        """Run the full batch pipeline and return summary counts."""
        print("=" * 60)
        print("  CineSub Studio - batch subtitle pipeline")
        print("=" * 60)
        print(f"  Model: {self.config.model}")
        print(f"  Device: {self.config.device}")
        print(f"  ASR mode: {self.config.asr_mode}")
        if self.config.language:
            print(f"  Fixed language: {self.config.language}")
        print(f"  Translation: {'enabled' if self.config.translate else 'disabled'}")
        if self.config.translate:
            print(f"  LLM: {self.config.llm_model}")
            print(f"  Target language: {self.config.target_language}")
            print(f"  Translation mode: {self.config.translation_mode}")
        print(f"  Subtitle formats: {','.join(self.config.subtitle_formats)}")
        if "ass" in self.config.subtitle_formats:
            print(f"  ASS: {ASS_RESERVED_MESSAGE}")
        print(f"  Input directory: {self.config.input_dir}")
        print(f"  Max retries: {self.config.max_retries}")
        print()

        print("Scanning input directory...")
        self.tasks = self.scan()

        if not self.tasks:
            print("No pending files found.")
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}

        print(f"Found {len(self.tasks)} pending file(s)\n")

        completed = 0
        failed = 0
        skipped = 0

        for i, task in enumerate(self.tasks, start=1):
            print(f"[{i}/{len(self.tasks)}] Processing: {task.file}")

            if task.status == "completed" and self.config.skip_completed:
                if self.completed_outputs_valid(task):
                    print("  Already completed with valid outputs, skipping")
                    skipped += 1
                    continue
                print("  Completed state has missing or empty final outputs, rebuilding")

            try:
                self._process_one(task)
                completed += 1
                print("  [OK] completed")
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                failed += 1
                task.status = "failed"
                task.error = str(exc)
                task.error_stage = task.stage
                task.save()
                print(f"  [FAILED] {exc}")
                traceback.print_exc()

        print()
        print("=" * 60)
        print("  Pipeline finished")
        print(f"  Total: {len(self.tasks)} | completed: {completed} | failed: {failed} | skipped: {skipped}")
        print("=" * 60)

        return {
            "total": len(self.tasks),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
        }

    def _process_one(self, task: TaskState) -> None:
        """Process a single task through all pipeline stages."""
        input_path = Path(task.input_path)
        stem = input_path.stem
        model = self.config.model
        outputs = plan_pipeline_outputs(
            output_root=self.config.output_dir,
            stem=stem,
            model=model,
            target_language=self.config.target_language,
            translation_mode=self.config.translation_mode,
        )
        if not task.audio_path or not _is_valid_output_file(Path(task.audio_path)):
            task.stage = TaskStage.EXTRACTING_AUDIO
            task.status = "running"
            task.save()
            print("  [1/5] Extracting audio...")
            task.audio_path = str(self._extract_audio(input_path))
            task.save()
        else:
            print("  [1/5] Audio already exists, skipping extraction")

        source_srt = outputs.source_srt
        signature_matches = task.asr_config_signature == self.config.asr_signature()
        transcribed_now = False
        if not _is_valid_output_file(source_srt) or not signature_matches:
            task.stage = TaskStage.TRANSCRIBING
            task.status = "running"
            task.save()
            print("  [2/5] Whisper transcription...")
            lang_info = self._transcribe(Path(task.audio_path), source_srt)
            task.source_srt = str(source_srt.resolve())
            task.language_detection = lang_info
            task.asr_review_report = str(lang_info.get("asr_review_report") or "")
            task.asr_review_summary = lang_info.get("asr_review_summary")
            task.asr_config_signature = self.config.asr_signature()
            task.save()
            transcribed_now = True
        else:
            task.stage = TaskStage.TRANSCRIBING
            task.status = "running"
            task.source_srt = str(source_srt.resolve())
            lang_json = source_srt.with_suffix(".lang.json")
            if lang_json.exists() and (
                task.language_detection is None
                or not task.asr_review_report
                or task.asr_review_summary is None
            ):
                try:
                    loaded_language = read_json(lang_json)
                    if task.language_detection is None:
                        task.language_detection = loaded_language
                    task.asr_review_report = str(
                        loaded_language.get("asr_review_report")
                        or task.asr_review_report
                        or ""
                    )
                    task.asr_review_summary = (
                        loaded_language.get("asr_review_summary")
                        or task.asr_review_summary
                    )
                except (OSError, json.JSONDecodeError):
                    pass
            task.save()
            print("  [2/5] Source SRT already exists, skipping transcription")

        if task.language_detection:
            ld = task.language_detection
            print(f"      language: {ld.get('source_language', '?')} "
                  f"(confidence: {ld.get('language_probability', 'N/A')})")

        if self.config.translate:
            translated_srt = outputs.translated_srt
            bilingual_srt = outputs.bilingual_srt
            output_translated = outputs.translation_output
            translated_now = False

            if transcribed_now or not _is_valid_output_file(output_translated):
                task.stage = TaskStage.TRANSLATING
                task.status = "running"
                task.save()
                print("  [3/5] LLM translation...")

                effective_prompt = self._build_language_strategy(task.language_detection)

                self._translate(
                    source_srt=source_srt,
                    output_path=output_translated,
                    effective_prompt=effective_prompt,
                )
                translated_now = True
                task.translated_srt = str(output_translated.resolve())
                task.bilingual_srt = str(bilingual_srt.resolve()) if self.config.translation_mode == "bilingual" else ""
                if self.config.translation_strategy_mode == "semantic_review":
                    candidate = output_translated.with_name(
                        f"{output_translated.stem}.semantic_review_report.json"
                    )
                    task.semantic_review_report = (
                        str(candidate.resolve()) if _is_valid_output_file(candidate) else ""
                    )
                elif self.config.translation_strategy_mode == "wenyi_review":
                    candidate = output_translated.with_name(
                        f"{output_translated.stem}.wenyi_review_report.json"
                    )
                    task.semantic_review_report = (
                        str(candidate.resolve()) if _is_valid_output_file(candidate) else ""
                    )
                elif self.config.translation_strategy_mode == "semantic_wenyi_review":
                    candidate = output_translated.with_name(
                        f"{output_translated.stem}.semantic_wenyi_review_report.json"
                    )
                    task.semantic_review_report = (
                        str(candidate.resolve()) if _is_valid_output_file(candidate) else ""
                    )
                task.save()
            else:
                task.translated_srt = str(output_translated.resolve())
                if self.config.translation_mode == "bilingual":
                    task.bilingual_srt = str(bilingual_srt.resolve())
                if self.config.translation_strategy_mode == "semantic_review":
                    candidate = output_translated.with_name(
                        f"{output_translated.stem}.semantic_review_report.json"
                    )
                    task.semantic_review_report = (
                        str(candidate.resolve()) if _is_valid_output_file(candidate) else ""
                    )
                elif self.config.translation_strategy_mode == "wenyi_review":
                    candidate = output_translated.with_name(
                        f"{output_translated.stem}.wenyi_review_report.json"
                    )
                    task.semantic_review_report = (
                        str(candidate.resolve()) if _is_valid_output_file(candidate) else ""
                    )
                elif self.config.translation_strategy_mode == "semantic_wenyi_review":
                    candidate = output_translated.with_name(
                        f"{output_translated.stem}.semantic_wenyi_review_report.json"
                    )
                    task.semantic_review_report = (
                        str(candidate.resolve()) if _is_valid_output_file(candidate) else ""
                    )
                task.save()
                print("  [3/5] Translated SRT already exists, skipping translation")

            report_path = outputs.quality_report
            if transcribed_now or translated_now or not _is_valid_output_file(report_path):
                task.stage = TaskStage.QUALITY_CHECKING
                task.status = "running"
                task.save()
                print("  [4/5] Quality check...")
                self._quality_check(source_srt, output_translated, report_path)
                task.quality_report = str(report_path.resolve())
                task.save()
            else:
                task.quality_report = str(report_path.resolve())
                task.save()
                print("  [4/5] Quality report already exists, skipping")
        else:
            print("  [3/5] Translation disabled, skipping")
            print("  [4/5] Quality check disabled, skipping")
            task.stage = TaskStage.QUALITY_CHECKING
            task.status = "running"
            task.save()

        task.stage = TaskStage.COMPLETED
        task.status = "completed"
        task.save()
        print("  [5/5] Outputs complete")

        print(f"      source: {task.source_srt}")
        if task.translated_srt:
            print(f"      translated: {task.translated_srt}")
        if "ass" in self.config.subtitle_formats:
            print(f"      ASS: {ASS_RESERVED_MESSAGE}")
        if task.quality_report:
            try:
                qr = read_json(task.quality_report)
                print(f"      quality: {qr.get('status', '?')} "
                      f"({qr.get('summary', {}).get('total_issues', 0)} issue(s))")
            except (OSError, json.JSONDecodeError):
                print(f"      quality: {task.quality_report}")

        if self.config.move_completed:
            self._archive_completed(task)


    def required_final_outputs(self, task: TaskState) -> list[Path]:
        """Return final outputs required before a completed task can be skipped."""
        return plan_required_final_outputs(task, self.config)

    def completed_outputs_valid(self, task: TaskState) -> bool:
        """Return True only when completed status and configured final outputs agree."""
        return (
            task.asr_config_signature == self.config.asr_signature()
            and validate_completed_outputs(task, self.config)
        )


    def _extract_audio(self, input_path: Path) -> Path:
        """Extract audio to a 16 kHz mono WAV file."""
        context = self._context(input_path)
        self._record_stage_started(context, TaskStage.EXTRACTING_AUDIO)
        try:
            result = extract_audio_stage(context, project_root=PROJECT_ROOT)
        except BaseException as exc:
            self._record_stage_error(context, TaskStage.EXTRACTING_AUDIO, exc)
            raise
        self._record_stage_result(context, result)
        return result.outputs[0]

    def _transcribe(self, audio_path: Path, srt_path: Path) -> dict | None:
        """Run Whisper transcription and return language detection details."""
        input_path = Path(audio_path)
        context = self._context(input_path)
        self._record_stage_started(context, TaskStage.TRANSCRIBING)
        try:
            result = transcribe_stage(
                context, audio_path=audio_path, srt_path=srt_path, config=self.config
            )
        except BaseException as exc:
            self._record_stage_error(context, TaskStage.TRANSCRIBING, exc)
            raise
        self._record_stage_result(context, result)
        return result.data.get("language_detection")

    def _translate(
        self,
        source_srt: Path,
        output_path: Path,
        effective_prompt: str,
    ) -> None:
        """Run LLM subtitle translation."""
        context = self._context(source_srt)
        self._record_stage_started(context, TaskStage.TRANSLATING)
        try:
            result = translate_stage(
                context,
                source_srt=source_srt,
                output_path=output_path,
                config=self.config,
                effective_prompt=effective_prompt,
            )
        except BaseException as exc:
            self._record_stage_error(context, TaskStage.TRANSLATING, exc)
            raise
        self._record_stage_result(context, result)

    def _quality_check(
        self,
        source_srt: Path,
        translated_srt: Path,
        report_path: Path,
    ) -> None:
        """Run quality checks using the active language profile thresholds."""
        context = self._context(source_srt)
        self._record_stage_started(context, TaskStage.QUALITY_CHECKING)
        try:
            result = quality_check_stage(
                context,
                source_srt=source_srt,
                translated_srt=translated_srt,
                report_path=report_path,
                config=self.config,
            )
        except BaseException as exc:
            self._record_stage_error(context, TaskStage.QUALITY_CHECKING, exc)
            raise
        self._record_stage_result(context, result)

    def _build_language_strategy(self, lang_detection: dict | None) -> str:
        """Confidence is diagnostic only and never changes translation behavior."""
        return self.config.translation_prompt

    def _archive_completed(self, task: TaskState) -> None:
        """Move a completed input file to the archive directory."""
        input_path = Path(task.input_path)
        try:
            context = self._context(input_path)
            self._record_stage_started(context, "archiving")
            result = archive_stage(context, archive_dir=DIR_ARCHIVE)
            self._record_stage_result(context, result)
            if result.outputs:
                print(f"      archived: {result.outputs[0].name}")
        except (OSError, StageError) as exc:
            self._record_stage_error(self._context(input_path), "archiving", exc)
            print(f"      archive failed: {exc}")



def main() -> int:
    parser = build_pipeline_parser()
    args = parser.parse_args()

    os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(PROJECT_ROOT / ".cache" / "huggingface" / "hub"))

    raw_argv = [arg.split("=", 1)[0] for arg in sys.argv[1:]]

    effective, config_messages = resolve_cli_config(args, raw_argv)
    for message in config_messages:
        print(message)

    if args.api_key:
        os.environ["SUBTITLE_LLM_API_KEY"] = args.api_key
    elif effective["api_key"]:
        os.environ["SUBTITLE_LLM_API_KEY"] = effective["api_key"]

    config = BatchConfig(
        input_dir=Path(args.input).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        model_dir=Path(args.model_dir).resolve(),
        work_dir=Path(args.work_dir).resolve(),
        model=effective["model"],
        device=effective["device"],
        compute_type=effective["compute_type"],
        asr_mode=effective["asr_mode"],
        language=effective["language"],
        beam_size=effective["beam_size"],
        vad_filter=effective["vad_filter"],
        local_files_only=args.local_files_only,
        quality_preset=effective["quality_preset"],
        word_timestamps=bool(effective["word_timestamps"]),
        resegment_subtitles=bool(effective["resegment_subtitles"]),
        asr_retry_mode=effective["asr_retry_mode"],
        asr_hotword_prompt=effective["asr_hotword_prompt"],
        effective_asr_config=effective["effective_asr_config"],
        translate=not args.no_translate,
        api_provider=effective["api_provider"],
        api_base=effective["api_base"],
        api_key=effective["api_key"],
        llm_model=effective["llm_model"],
        translation_quality_model=effective["translation_quality_model"],
        target_language=effective["target_language"],
        translation_prompt=effective["translation_prompt"],
        translation_batch_size=args.translation_batch_size,
        translation_temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        context_window=args.context_window,
        translation_reliability_mode=effective["translation_reliability"]["mode"],
        translation_max_extra_requests=effective["translation_reliability"]["max_extra_requests"],
        translation_strategy_mode=effective["translation_strategy"]["mode"],
        translation_scene_gap_seconds=effective["translation_strategy"]["scene_gap_seconds"],
        subtitle_formats=effective["subtitle_formats"],
        ass_style_id=effective["ass_style_id"],
        subtitle_style=effective["subtitle_style"],
        language_profile_id=effective["profile_info"].get("profile_id", ""),
        language_profile_name=effective["profile_info"].get("profile_name", ""),
        lang_profile_config=effective["profile_info"],
        max_retries=args.max_retries,
        skip_completed=not args.no_skip_completed,
        move_completed=not args.no_move_completed,
    )

    pipeline = BatchPipeline(config)

    if args.scan:
        tasks = pipeline.scan()
        if not tasks:
            print("No pending files found.")
            return 0
        print(f"\nPending files ({len(tasks)}):")
        for t in tasks:
            status_mark = {"completed": "[OK]", "failed": "[FAILED]", "pending": "[ ]"}.get(t.status, "[?]")
            print(f"  {status_mark} {t.file} - {t.stage}")
        return 0

    if args.status:
        return show_status(DIR_WORK_STATES)

    if args.retry_failed:
        return _retry_failed(pipeline)

    if args.review:
        return show_review(config.output_dir)

    if args.review_file:
        return show_review_detail(Path(args.review_file))

    api_key = effective["api_key"] or os.environ.get("SUBTITLE_LLM_API_KEY", "")
    if config.translate and not api_key:
        print("Warning: translation is enabled but no API key is configured.")
        print("Set a provider, pass --api-key, set SUBTITLE_LLM_API_KEY, or use --no-translate.")
        return 1

    result = pipeline.run()
    return 0 if result["failed"] == 0 else 1


def _show_status() -> int:
    return show_status(DIR_WORK_STATES)


def _retry_failed(pipeline: BatchPipeline) -> int:
    """Retry only tasks currently marked as failed."""
    if not DIR_WORK_STATES.exists():
        print("No task records found.")
        return 0

    state_files = sorted(DIR_WORK_STATES.glob("*.state.json"))
    retry_plan = prepare_retry_failed_tasks(state_files)
    for task in retry_plan.reset_tasks:
        print(f"  reset: {task.file}")

    if not retry_plan.reset_tasks:
        print("No failed tasks need retry.")
        return 0

    print(
        f"\nReset {retry_plan.reset_count} failed task(s); "
        f"left {retry_plan.untouched_count} non-failed task(s) untouched; "
        "retrying without scanning new files.\n"
    )

    completed = 0
    failed = 0
    for i, task in enumerate(retry_plan.reset_tasks, start=1):
        print(f"[{i}/{retry_plan.reset_count}] Retrying: {task.file}")
        try:
            pipeline._process_one(task)
            completed += 1
            print("  [OK] completed")
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            failed += 1
            task.status = "failed"
            task.error = str(exc)
            task.error_stage = task.stage
            task.save()
            print(f"  [FAILED] {exc}")
            traceback.print_exc()

    print(f"\nRetry finished: completed {completed}, failed {failed}")
    return 0 if failed == 0 else 1


def _safe_console_print(text: str = "") -> None:
    safe_console_print(text)


def _show_review(output_root: Path = DIR_OUTPUT) -> int:
    return show_review(output_root)

def _show_review_detail(report_path: Path) -> int:
    return show_review_detail(report_path)

if __name__ == "__main__":
    raise SystemExit(main())
