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
import os
import sys
import time
import traceback
import uuid
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
    RetryPlanChanged,
    TaskStage,
    TaskState,
    completed_outputs_valid as validate_completed_outputs,
    is_valid_output_file as _is_valid_output_file,
    apply_retry_failed_plan,
    plan_retry_failed_tasks,
    required_final_outputs as plan_required_final_outputs,
    set_state_root_provider,
)
from pipeline_reliability import (
    PipelinePlan,
    PipelineRunLock,
    PipelineTaskPlan,
    artifact_fingerprint,
    artifact_set_fingerprint,
    artifact_set_matches,
    build_pipeline_plan as build_read_only_pipeline_plan,
    canonical_hash,
    collect_local_pipeline_preflight,
    expected_stage_signatures,
    windows_process_creation_filetime,
    read_run_record,
    retry_fingerprint,
    write_run_record,
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
    provider_id: str = ""
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
        return canonical_hash(self.asr_signature_payload())

    def asr_signature_payload(self) -> dict:
        return {
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


def build_pipeline_plan(config: BatchConfig, *, read_only: bool = True) -> PipelinePlan:
    """Build the shared read-only plan without constructing BatchPipeline."""
    return build_read_only_pipeline_plan(
        config,
        state_dir=DIR_WORK_STATES,
        video_extensions=VIDEO_EXTENSIONS,
        read_only=read_only,
    )



class BatchPipeline:
    """Batch subtitle production pipeline."""

    def __init__(self, config: BatchConfig, *, plan: PipelinePlan | None = None, run_id: str = ""):
        self.config = config
        self.tasks: list[TaskState] = []
        self.plan = plan
        self.run_id = run_id
        self._current_task: TaskState | None = None
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
            task_id=(self._current_task.task_id if self._current_task else input_path.name),
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
            run_id=self.run_id,
        )

    def _record_stage_started(self, context: TaskContext, stage: str) -> None:
        self._update_run_progress(context.task_id, stage)
        write_stage_event(
            STAGE_EVENT_LOG,
            task_id=context.task_id,
            stage=stage,
            event="started",
            status="running",
            run_id=self.run_id,
        )

    def _update_run_progress(self, task_id: str, stage: str) -> None:
        if not self.run_id:
            return
        path = self.config.work_dir / "pipeline_run.json"
        record = read_run_record(path)
        if record.get("run_id") != self.run_id:
            return
        write_run_record(path, {
            **record,
            "current_task_id": task_id,
            "current_stage": stage,
        })

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
            run_id=self.run_id,
        )

    def scan(self) -> list[TaskState]:
        """Return a strictly read-only view of tasks selected by the current plan."""
        plan = build_pipeline_plan(self.config, read_only=True)
        return [self._task_from_plan(item, persist=False) for item in plan.tasks if item.category != "skip"]

    def _task_from_plan(self, item: PipelineTaskPlan, *, persist: bool) -> TaskState:
        state_path = Path(item.state_path)
        legacy_path = Path(item.legacy_state_path) if item.legacy_state_path else None
        task = TaskState.load(state_path)
        if task is None and legacy_path:
            task = TaskState.load(legacy_path)
        if task is None:
            task = TaskState(
                file=item.display_name,
                input_path=item.input_path,
                created_at=time.time(),
            )
        task.task_id = item.task_id
        task.file = item.display_name
        task.input_path = item.input_path
        task.current_input_path = item.input_path
        task.original_relative_path = item.relative_input_path
        task.output_stem = item.output_stem
        task.input_location = "active"
        task.input_fingerprint = item.input_fingerprint
        task.stage_build_signatures["input"] = item.expected_signatures["input"]
        task.max_retries = self.config.max_retries
        task.asr_mode = self.config.asr_mode
        task.language = self.config.language or ""
        task.run_id = self.run_id
        if persist:
            task.save()
            if legacy_path and legacy_path != task.state_path() and legacy_path.exists():
                legacy_path.unlink()
        return task

    def _materialize_plan(self, plan: PipelinePlan) -> list[TaskState]:
        if plan.blockers:
            raise ValueError("Pipeline preflight failed: " + "; ".join(row.message for row in plan.blockers))
        return [self._task_from_plan(item, persist=True) for item in plan.tasks]

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

        print("Planning input directory...")
        plan = self.plan or build_pipeline_plan(self.config, read_only=True)
        self.tasks = self._materialize_plan(plan)

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
                self._current_task = task
                self._process_one(task)
                completed += 1
                print("  [OK] completed")
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                failed += 1
                task.status = "failed"
                task.error = str(exc)
                task.error_category = type(exc).__name__
                task.error_stage = task.stage
                task.save()
                print(f"  [FAILED] {exc}")
                traceback.print_exc()
            finally:
                self._current_task = None

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
        stem = task.output_stem or input_path.stem
        model = self.config.model
        outputs = plan_pipeline_outputs(
            output_root=self.config.output_dir,
            stem=stem,
            model=model,
            target_language=self.config.target_language,
            translation_mode=self.config.translation_mode,
        )
        expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
        audio_cached = task.artifact_fingerprints.get("audio") or {}
        audio_current = artifact_fingerprint(Path(task.audio_path), audio_cached) if task.audio_path else None
        audio_valid = bool(
            audio_current
            and (
                not audio_cached
                or audio_current.get("sha256") == audio_cached.get("sha256")
            )
            and (
                not task.stage_build_signatures.get("audio")
                or task.stage_build_signatures.get("audio") == expected["audio"]
            )
        )
        if not audio_valid:
            task.stage = TaskStage.EXTRACTING_AUDIO
            task.status = "running"
            task.save()
            print("  [1/5] Extracting audio...")
            task.audio_path = str(self._extract_audio(input_path))
            task.artifact_fingerprints["audio"] = artifact_fingerprint(Path(task.audio_path), force_full=True)
            task.stage_build_signatures["audio"] = expected["audio"]
            task.save()
        else:
            task.artifact_fingerprints["audio"] = audio_current
            task.stage_build_signatures["audio"] = expected["audio"]
            task.save()
            print("  [1/5] Audio already exists, skipping extraction")

        source_srt = outputs.source_srt
        expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
        source_cached = task.artifact_fingerprints.get("source_srt") or {}
        source_current = artifact_fingerprint(source_srt, source_cached)
        signature_matches = task.stage_build_signatures.get("asr") == expected["asr"]
        if not task.stage_build_signatures.get("asr") and task.asr_config_signature == self.config.asr_signature():
            signature_matches = bool(source_current)
        transcribed_now = False
        source_integrity = bool(
            source_current
            and (
                not source_cached
                or source_current.get("sha256") == source_cached.get("sha256")
            )
        )
        if not source_integrity or not signature_matches:
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
            task.artifact_fingerprints["source_srt"] = artifact_fingerprint(source_srt, force_full=True)
            expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
            task.stage_build_signatures["asr"] = expected["asr"]
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
            task.artifact_fingerprints["source_srt"] = source_current
            task.stage_build_signatures["asr"] = expected["asr"]
            task.asr_config_signature = self.config.asr_signature()
            task.save()

        if task.language_detection:
            ld = task.language_detection
            print(f"      language: {ld.get('source_language', '?')} "
                  f"(confidence: {ld.get('language_probability', 'N/A')})")

        if self.config.translate:
            translated_srt = outputs.translated_srt
            bilingual_srt = outputs.bilingual_srt
            output_translated = outputs.translation_output
            translated_now = False

            expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
            translated_cached = task.artifact_fingerprints.get("translation_output") or {}
            translated_current = artifact_fingerprint(output_translated, translated_cached)
            translation_valid = bool(
                translated_current
                and translated_current.get("sha256") == translated_cached.get("sha256")
                and task.stage_build_signatures.get("translation") == expected["translation"]
            )
            if transcribed_now or not translation_valid:
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
                task.artifact_fingerprints["translation_output"] = artifact_fingerprint(
                    output_translated, force_full=True
                )
                expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
                task.stage_build_signatures["translation"] = expected["translation"]
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
            expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
            quality_cached = task.artifact_fingerprints.get("quality_report") or {}
            quality_current = artifact_fingerprint(report_path, quality_cached)
            quality_valid = bool(
                quality_current
                and quality_current.get("sha256") == quality_cached.get("sha256")
                and task.stage_build_signatures.get("quality") == expected["quality"]
            )
            if transcribed_now or translated_now or not quality_valid:
                task.stage = TaskStage.QUALITY_CHECKING
                task.status = "running"
                task.save()
                print("  [4/5] Quality check...")
                self._quality_check(source_srt, output_translated, report_path)
                task.quality_report = str(report_path.resolve())
                task.artifact_fingerprints["quality_report"] = artifact_fingerprint(
                    report_path, force_full=True
                )
                expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
                task.stage_build_signatures["quality"] = expected["quality"]
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
        task.artifact_fingerprints["final_output"] = artifact_set_fingerprint(
            self.required_final_outputs(task)
        )
        expected = expected_stage_signatures(self.config, task.to_dict(), task.input_fingerprint)
        task.stage_build_signatures["final_output"] = expected["final_output"]
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
        if not validate_completed_outputs(task, self.config):
            return False
        if not artifact_set_matches(task.artifact_fingerprints.get("final_output")):
            return False
        expected = expected_stage_signatures(
            self.config, task.to_dict(), task.input_fingerprint or {}
        )
        required = ["input", "audio", "asr"] + (["translation", "quality", "final_output"] if self.config.translate else ["final_output"])
        return all(task.stage_build_signatures.get(stage) == expected.get(stage) for stage in required)


    def _extract_audio(self, input_path: Path, *, reuse_existing: bool = False) -> Path:
        """Extract audio to a 16 kHz mono WAV file."""
        context = self._context(input_path)
        self._record_stage_started(context, TaskStage.EXTRACTING_AUDIO)
        try:
            result = extract_audio_stage(
                context, project_root=PROJECT_ROOT, reuse_existing=reuse_existing
            )
        except BaseException as exc:
            self._record_stage_error(context, TaskStage.EXTRACTING_AUDIO, exc)
            raise
        self._record_stage_result(context, result)
        return result.outputs[0]

    def _transcribe(
        self, audio_path: Path, srt_path: Path, *, reuse_existing: bool = False
    ) -> dict | None:
        """Run Whisper transcription and return language detection details."""
        input_path = Path(audio_path)
        context = self._context(input_path)
        self._record_stage_started(context, TaskStage.TRANSCRIBING)
        try:
            result = transcribe_stage(
                context,
                audio_path=audio_path,
                srt_path=srt_path,
                config=self.config,
                reuse_existing=reuse_existing,
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
                archived = result.outputs[0].resolve()
                task.input_location = "archive"
                task.current_input_path = str(archived)
                task.archived_at = time.time()
                task.archive_warning = ""
                task.save()
                print(f"      archived: {result.outputs[0].name}")
        except (OSError, StageError) as exc:
            self._record_stage_error(self._context(input_path), "archiving", exc)
            task.archive_warning = str(exc)
            task.save()
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
        provider_id=effective["provider_id"],
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

    if args.scan:
        plan = build_pipeline_plan(config, read_only=True)
        if plan.blockers:
            for blocker in plan.blockers:
                print(f"  [blocker:{blocker.code}] {blocker.message}")
            return 1
        if not plan.tasks:
            print("No pending files found.")
            return 0
        print(f"\nPipeline plan ({len(plan.tasks)}):")
        for item in plan.tasks:
            migrations = []
            if item.planned_migration:
                migrations.append("state")
            if item.planned_asr_signature_migration:
                migrations.append("asr-signature")
            migration = f" planned-migration={','.join(migrations)}" if migrations else ""
            print(f"  [{item.category}] {item.display_name}{migration}")
        return 0

    if args.status:
        return show_status(DIR_WORK_STATES)

    if args.review:
        return show_review(config.output_dir)

    if args.review_file:
        return show_review_detail(Path(args.review_file))

    api_key = effective["api_key"] or os.environ.get("SUBTITLE_LLM_API_KEY", "")
    if config.translate and not api_key:
        print("Warning: translation is enabled but no API key is configured.")
        print("Set a provider, pass --api-key, set SUBTITLE_LLM_API_KEY, or use --no-translate.")
        return 1

    run_id = os.environ.get("CINESUB_PIPELINE_RUN_ID", "") or uuid.uuid4().hex
    run_record_path = config.work_dir / "pipeline_run.json"
    lock = None
    worker_lease = None
    handoff_lock_path = os.environ.get("CINESUB_PIPELINE_LOCK_PATH", "")
    if handoff_lock_path:
        worker_lease = PipelineRunLock(Path(handoff_lock_path), offset=1)
        deadline = time.monotonic() + 10.0
        while not worker_lease.acquire() and time.monotonic() < deadline:
            time.sleep(0.02)
        if worker_lease._handle is None:
            print("Could not acquire the pipeline worker lease.")
            return 1
        ack_path = Path(os.environ.get("CINESUB_PIPELINE_LOCK_ACK", ""))
        if ack_path:
            ack_path.write_text(run_id, encoding="ascii")
    else:
        lock = PipelineRunLock(config.work_dir / "pipeline_run.lock")
        if not lock.acquire():
            print("Another pipeline process is already running.")
            return 1
    action = "retry-failed" if args.retry_failed else "run"
    server_pid = int(os.environ.get("CINESUB_PIPELINE_SERVER_PID", "0") or 0)
    expected_plan = os.environ.get("CINESUB_PIPELINE_EXPECTED_PLAN", "")

    def _abort_plan_changed(
        abort_reason: str,
        *,
        expected_plan: str = "",
        observed_plan: str = "",
        changed_task_id: str = "",
    ) -> None:
        """Write an aborted_plan_changed terminal record without touching task state.

        Merges over the Web's ``preparing`` record (or the prior ``running``
        record) so ``get_pipeline_task`` never marks the run stale. Full
        fingerprints live only in the private run record; the API surface
        (Task #9) returns ``status`` + ``abort_reason`` + a short prefix only.
        """
        base = read_run_record(run_record_path) or {}
        base.update({
            "schema_version": 1,
            "run_id": run_id,
            "action": action,
            "status": "aborted_plan_changed",
            "abort_reason": abort_reason,
            "server_pid": server_pid,
            "worker_pid": os.getpid(),
            "worker_creation_filetime": windows_process_creation_filetime(os.getpid()),
            "plan_fingerprint": observed_plan,
            "expected_plan_fingerprint": expected_plan,
            "observed_plan_fingerprint": observed_plan,
            "changed_task_id": changed_task_id,
            "task_ids": base.get("task_ids", []),
            "current_task_id": "",
            "current_stage": "",
            "started_at": base.get("started_at", time.time()),
            "finished_at": time.time(),
            "counts": {},
            "failure_stage_counts": {},
        })
        write_run_record(run_record_path, base)
        print(f"Pipeline aborted ({abort_reason}); see run record.")

    try:
        try:
            plan = build_pipeline_plan(config, read_only=True)
            findings = collect_local_pipeline_preflight(config, plan)
            # Only abort on a missing model when the worker cannot download it
            # (local_files_only) or was spawned by a Web preflight that already
            # verified availability (expected_plan). A direct CLI run with
            # downloads allowed preserves the prior faster-whisper behavior.
            if findings["model_missing"] and (
                bool(expected_plan) or bool(getattr(config, "local_files_only", False))
            ):
                _abort_plan_changed(
                    "model_unavailable_after_preflight",
                    expected_plan=expected_plan,
                )
                return 1
            if findings["blockers"]:
                for blocker in findings["blockers"]:
                    print(f"  [blocker:{blocker.code}] {blocker.message}")
                _abort_plan_changed(
                    "new_blocker_after_preflight",
                    expected_plan=expected_plan,
                )
                return 1
            if action == "run":
                task_ids = [item.task_id for item in plan.tasks]
                observed_plan = plan.plan_fingerprint
                retry_plan = None
            else:
                active_task_ids = {item.task_id for item in plan.tasks}
                retry_plan = plan_retry_failed_tasks(
                    sorted(DIR_WORK_STATES.glob("*.state.json")),
                    allowed_task_ids=active_task_ids,
                )
                task_ids = retry_plan.selected_task_ids
                observed_plan = retry_fingerprint(plan, task_ids)
            # Fingerprint compare BEFORE empty-set handling: drift (incl. a
            # changed failed-task set) aborts as plan_fingerprint_mismatch
            # rather than silently no-op'ing.
            if expected_plan and observed_plan != expected_plan:
                _abort_plan_changed(
                    "plan_fingerprint_mismatch",
                    expected_plan=expected_plan,
                    observed_plan=observed_plan,
                )
                return 1
            if not task_ids and not expected_plan:
                write_run_record(run_record_path, {
                    "schema_version": 1,
                    "run_id": run_id,
                    "action": action,
                    "status": "completed",
                    "server_pid": server_pid,
                    "worker_pid": os.getpid(),
                    "worker_creation_filetime": windows_process_creation_filetime(os.getpid()),
                    "plan_fingerprint": observed_plan,
                    "effective_config_hash": plan.effective_config_hash,
                    "task_ids": [],
                    "current_task_id": "",
                    "current_stage": "",
                    "started_at": time.time(),
                    "finished_at": time.time(),
                    "counts": {},
                    "failure_stage_counts": {},
                })
                print("No pending files found." if action == "run" else "No failed tasks need retry.")
                return 0
            record = write_run_record(run_record_path, {
                "schema_version": 1,
                "run_id": run_id,
                "action": action,
                "status": "running",
                "server_pid": server_pid,
                "worker_pid": os.getpid(),
                "worker_creation_filetime": windows_process_creation_filetime(os.getpid()),
                "plan_fingerprint": observed_plan,
                "effective_config_hash": plan.effective_config_hash,
                "task_ids": task_ids,
                "current_task_id": "",
                "current_stage": "",
                "started_at": time.time(),
                "finished_at": 0,
                "counts": {},
                "failure_stage_counts": {},
            })
            pipeline = BatchPipeline(config, plan=plan, run_id=run_id)
            if action == "retry-failed":
                try:
                    apply_retry_failed_plan(retry_plan, run_id=run_id)
                except RetryPlanChanged as exc:
                    _abort_plan_changed(
                        "retry_state_changed",
                        expected_plan=expected_plan,
                        observed_plan=observed_plan,
                        changed_task_id=exc.task_id,
                    )
                    return 1
        except Exception:
            _abort_plan_changed(
                "configuration_unavailable_after_preflight",
                expected_plan=expected_plan,
            )
            return 1
        if action == "retry-failed":
            returncode = _retry_failed(pipeline, run_id=run_id, retry_plan=retry_plan)
            counts = {"failed": int(returncode != 0)}
        else:
            result = pipeline.run()
            returncode = 0 if result["failed"] == 0 else 1
            counts = result
        failure_stage_counts: dict[str, int] = {}
        for task in pipeline.tasks:
            if task.status == "failed":
                stage = task.error_stage or task.stage or "unknown"
                failure_stage_counts[stage] = failure_stage_counts.get(stage, 0) + 1
        write_run_record(run_record_path, {
            **record,
            "status": "completed" if returncode == 0 else "failed",
            "finished_at": time.time(),
            "counts": counts,
            "failure_stage_counts": failure_stage_counts,
            "current_task_id": "",
            "current_stage": "",
        })
        return returncode
    finally:
        if lock:
            lock.release()
        if worker_lease:
            worker_lease.release()


def _show_status() -> int:
    return show_status(DIR_WORK_STATES)


def _retry_failed(
    pipeline: BatchPipeline,
    *,
    run_id: str = "",
    retry_plan: RetryPlan | None = None,
) -> int:
    """Retry only tasks currently marked as failed.

    When ``retry_plan`` is supplied (Web-spawned worker), the caller has already
    built the plan (intersected to active inputs) and applied the two-phase
    reset; here we only execute. Otherwise (direct CLI) we plan + apply here.
    """
    if retry_plan is None:
        if not DIR_WORK_STATES.exists():
            print("No task records found.")
            return 0
        state_files = sorted(DIR_WORK_STATES.glob("*.state.json"))
        retry_plan = plan_retry_failed_tasks(state_files)
        if not retry_plan.selected_tasks:
            print("No failed tasks need retry.")
            return 0
        apply_retry_failed_plan(retry_plan, run_id=run_id)
    pipeline.tasks = list(retry_plan.selected_tasks)
    for task in retry_plan.selected_tasks:
        print(f"  reset: {task.file}")

    print(
        f"\nReset {retry_plan.reset_count} failed task(s); "
        f"left {retry_plan.untouched_count} non-failed task(s) untouched; "
        "retrying without scanning new files.\n"
    )

    completed = 0
    failed = 0
    for i, task in enumerate(retry_plan.selected_tasks, start=1):
        print(f"[{i}/{retry_plan.reset_count}] Retrying: {task.file}")
        try:
            pipeline._current_task = task
            pipeline._process_one(task)
            completed += 1
            print("  [OK] completed")
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            failed += 1
            task.status = "failed"
            task.error = str(exc)
            task.error_category = type(exc).__name__
            task.error_stage = task.stage
            task.save()
            print(f"  [FAILED] {exc}")
            traceback.print_exc()
        finally:
            pipeline._current_task = None

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
