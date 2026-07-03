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
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Ensure src subdirectories are on sys.path for cross-module imports when run directly
_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "pipeline", "config", "web", "tools"):
    _subpath = str(_src / _sub)
    if _subpath not in sys.path:
        sys.path.insert(0, _subpath)

from encoding_utils import read_json, run_text, write_json
from ffmpeg_locator import find_ffmpeg
from output_paths import pipeline_output_dirs, plan_pipeline_outputs
from runtime_paths import resolve_runtime_paths
from subtitle_model import (
    ASS_RESERVED_MESSAGE,
    DEFAULT_ASS_STYLE_ID,
    normalize_subtitle_formats,
)

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


class TaskStage:
    PENDING = "pending"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    QUALITY_CHECKING = "quality_checking"
    COMPLETED = "completed"
    FAILED = "failed"



MAJOR_LANGUAGES = {"en", "ja", "ko", "zh", "fr", "de", "es", "ru", "pt", "it", "ar", "th", "vi"}

DEFAULT_MAJOR_PROMPT = ""

MINOR_LANGUAGE_EXTRA_PROMPT = (
    "The source language may be low-resource, dialectal, or uncertain. "
    "Preserve uncertain names and terms, and mark unclear content as [needs review]."
)

LOW_CONFIDENCE_EXTRA_PROMPT = (
    "The detected source language has low confidence. "
    "Do not force a translation for garbled or incomplete source text."
)

LANG_CONFIDENCE_THRESHOLD = 0.7


def _is_valid_output_file(path: Path) -> bool:
    """Return True only for existing non-empty stage outputs."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False



@dataclass
class TaskState:
    """State for one input media task."""
    file: str
    input_path: str
    stage: str = TaskStage.PENDING
    status: str = "pending"      # pending | running | completed | failed
    created_at: float = 0.0
    updated_at: float = 0.0

    audio_path: str = ""

    language_detection: dict | None = None

    source_srt: str = ""

    translated_srt: str = ""
    bilingual_srt: str = ""

    quality_report: str = ""

    error: str = ""
    error_stage: str = ""
    retry_count: int = 0
    max_retries: int = 3

    output_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "input_path": self.input_path,
            "stage": self.stage,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "audio_path": self.audio_path,
            "language_detection": self.language_detection,
            "source_srt": self.source_srt,
            "translated_srt": self.translated_srt,
            "bilingual_srt": self.bilingual_srt,
            "quality_report": self.quality_report,
            "error": self.error,
            "error_stage": self.error_stage,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "output_dir": self.output_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState":
        return cls(
            file=data.get("file", ""),
            input_path=data.get("input_path", ""),
            stage=data.get("stage", TaskStage.PENDING),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            audio_path=data.get("audio_path", ""),
            language_detection=data.get("language_detection"),
            source_srt=data.get("source_srt", ""),
            translated_srt=data.get("translated_srt", ""),
            bilingual_srt=data.get("bilingual_srt", ""),
            quality_report=data.get("quality_report", ""),
            error=data.get("error", ""),
            error_stage=data.get("error_stage", ""),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            output_dir=data.get("output_dir", ""),
        )

    def state_path(self) -> Path:
        """Return the task state file path."""
        stem = Path(self.file).stem
        return DIR_WORK_STATES / f"{stem}.state.json"

    def save(self) -> None:
        """Save task state to JSON."""
        self.updated_at = time.time()
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, self.to_dict())

    @classmethod
    def load(cls, state_path: Path) -> Optional["TaskState"]:
        """Load task state from JSON."""
        if not state_path.exists():
            return None
        try:
            data = read_json(state_path)
            return cls.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None



@dataclass
class RetryPlan:
    """Structured result for failed-task retry preparation."""
    reset_tasks: list[TaskState] = field(default_factory=list)
    untouched_count: int = 0
    selected_task_ids: list[str] = field(default_factory=list)

    @property
    def reset_count(self) -> int:
        return len(self.reset_tasks)

    def to_dict(self) -> dict:
        return {
            "reset_count": self.reset_count,
            "untouched_count": self.untouched_count,
            "selected_task_ids": list(self.selected_task_ids),
        }


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



def prepare_retry_failed_tasks(state_files: list[Path]) -> RetryPlan:
    """Reset only failed tasks and leave every other state untouched."""
    plan = RetryPlan()

    for state_file in state_files:
        task = TaskState.load(state_file)
        if task is None:
            continue

        if task.status != "failed":
            plan.untouched_count += 1
            continue

        task.status = "pending"
        task.stage = TaskStage.PENDING
        task.error = ""
        task.error_stage = ""
        task.retry_count = 0
        task.save()
        plan.reset_tasks.append(task)
        plan.selected_task_ids.append(task.file)

    return plan


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
    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = True
    local_files_only: bool = False

    translate: bool = True
    api_provider: str = "openai-compatible"
    api_base: str = ""
    api_key: str = ""
    llm_model: str = ""
    target_language: str = "zh-CN"
    translation_batch_size: int = 20
    translation_temperature: float = 0.2
    translation_mode: str = "bilingual"
    context_window: int = 3
    translation_prompt: str = ""
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
        if not self.api_key:
            self.api_key = os.environ.get("SUBTITLE_LLM_API_KEY", "")



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
        if not _is_valid_output_file(source_srt):
            task.stage = TaskStage.TRANSCRIBING
            task.status = "running"
            task.save()
            print("  [2/5] Whisper transcription...")
            lang_info = self._transcribe(Path(task.audio_path), source_srt)
            task.source_srt = str(source_srt.resolve())
            task.language_detection = lang_info
            task.save()
        else:
            task.stage = TaskStage.TRANSCRIBING
            task.status = "running"
            task.source_srt = str(source_srt.resolve())
            lang_json = source_srt.with_suffix(".lang.json")
            if lang_json.exists() and task.language_detection is None:
                try:
                    task.language_detection = read_json(lang_json)
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

            if not _is_valid_output_file(output_translated):
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
                task.translated_srt = str(output_translated.resolve())
                task.bilingual_srt = str(bilingual_srt.resolve()) if self.config.translation_mode == "bilingual" else ""
                task.save()
            else:
                task.translated_srt = str(output_translated.resolve())
                if self.config.translation_mode == "bilingual":
                    task.bilingual_srt = str(bilingual_srt.resolve())
                task.save()
                print("  [3/5] Translated SRT already exists, skipping translation")

            report_path = outputs.quality_report
            if not _is_valid_output_file(report_path):
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
        input_path = Path(task.input_path or task.file)
        outputs = plan_pipeline_outputs(
            output_root=self.config.output_dir,
            stem=input_path.stem,
            model=self.config.model,
            target_language=self.config.target_language,
            translation_mode=self.config.translation_mode,
        )
        required = [outputs.source_srt]
        if self.config.translate:
            required.extend([outputs.translation_output, outputs.quality_report])
        return required

    def completed_outputs_valid(self, task: TaskState) -> bool:
        """Return True only when completed status and configured final outputs agree."""
        if task.status != "completed":
            return False
        return all(_is_valid_output_file(path) for path in self.required_final_outputs(task))


    def _extract_audio(self, input_path: Path) -> Path:
        """Extract audio to a 16 kHz mono WAV file."""
        audio_path = self.config.work_dir / f"{input_path.stem}.16k.wav"

        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path

        suffix = input_path.suffix.lower()
        if suffix == ".wav":
            pass

        ffmpeg = find_ffmpeg(PROJECT_ROOT)
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg was not found. Put ffmpeg.exe in tools/ffmpeg/bin/, "
                "run src/tools/download_ffmpeg.py, or set CINESUB_FFMPEG."
            )

        command = [
            ffmpeg, "-y", "-i", str(input_path),
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            str(audio_path),
        ]
        result = run_text(command, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[:500]}")

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError("Audio extraction failed: output file is empty.")

        return audio_path

    def _transcribe(self, audio_path: Path, srt_path: Path) -> dict | None:
        """Run Whisper transcription and return language detection details."""
        from transcribe import transcribe_to_srt

        lp_id = self.config.language_profile_id if hasattr(self.config, 'language_profile_id') else ""
        lp_name = self.config.language_profile_name if hasattr(self.config, 'language_profile_name') else ""
        lp_cond = True
        if hasattr(self.config, 'lang_profile_config') and self.config.lang_profile_config:
            lp_cfg = self.config.lang_profile_config
            lp_id = lp_cfg.get('profile_id', lp_id)
            lp_name = lp_cfg.get('profile_name', lp_name)

        lang_info = transcribe_to_srt(
            audio_path=audio_path,
            srt_path=srt_path,
            model_name=self.config.model,
            model_dir=self.config.model_dir,
            device=self.config.device,
            compute_type=self.config.compute_type,
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            local_files_only=self.config.local_files_only,
            language_profile_id=lp_id,
            language_profile_name=lp_name,
            condition_on_previous_text=lp_cond,
        )
        return lang_info

    def _translate(
        self,
        source_srt: Path,
        output_path: Path,
        effective_prompt: str,
    ) -> None:
        """Run LLM subtitle translation."""
        from subtitle_translate import translate_srt

        translate_srt(
            input_path=source_srt,
            output_path=output_path,
            api_provider=self.config.api_provider,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
            llm_model=self.config.llm_model,
            target_language=self.config.target_language,
            batch_size=self.config.translation_batch_size,
            temperature=self.config.translation_temperature,
            translation_mode=self.config.translation_mode,
            system_prompt=effective_prompt,
            context_window=self.config.context_window,
        )

    def _quality_check(
        self,
        source_srt: Path,
        translated_srt: Path,
        report_path: Path,
    ) -> None:
        """Run quality checks using the active language profile thresholds."""
        from quality_checker import run_quality_check

        thresholds = {}
        if self.config.lang_profile_config:
            thresholds = self.config.lang_profile_config.get("quality_thresholds", {})
            if not thresholds:
                thresholds = {}

        run_quality_check(
            source_srt=source_srt,
            translated_srt=translated_srt,
            target_language=self.config.target_language,
            output_dir=report_path.parent,
            quality_thresholds=thresholds,
        )

    def _build_language_strategy(self, lang_detection: dict | None) -> str:
        """Build the translation strategy prompt from language detection."""
        if not lang_detection:
            return self.config.translation_prompt

        lang = lang_detection.get("source_language", "")
        prob = lang_detection.get("language_probability")

        extra_parts: list[str] = []

        if self.config.translation_prompt.strip():
            extra_parts.append(self.config.translation_prompt.strip())

        if lang and lang not in MAJOR_LANGUAGES:
            extra_parts.append(MINOR_LANGUAGE_EXTRA_PROMPT)

        if prob is not None and prob < LANG_CONFIDENCE_THRESHOLD:
            extra_parts.append(LOW_CONFIDENCE_EXTRA_PROMPT)

        if extra_parts:
            return "\n\n".join(extra_parts)

        return ""

    def _archive_completed(self, task: TaskState) -> None:
        """Move a completed input file to the archive directory."""
        input_path = Path(task.input_path)
        if not input_path.exists():
            return

        dest = DIR_ARCHIVE / input_path.name
        if dest.exists():
            dest = DIR_ARCHIVE / f"{input_path.stem}_{int(time.time())}{input_path.suffix}"

        try:
            shutil.move(str(input_path), str(dest))
            print(f"      archived: {dest.name}")
        except OSError as exc:
            print(f"      archive failed: {exc}")



def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="CineSub Studio batch subtitle pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
Examples:
  .\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --input input --model large-v3 --device cuda
  .\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --input input --model small --no-translate
  .\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --scan
  .\.venv\Scripts\python.exe -B src\pipeline\batch_worker.py --status
        """.strip(),
    )

    parser.add_argument("--input", default="input", help="Input media directory (default: input/)")
    parser.add_argument("--output-dir", default="output", help="Output root directory (default: output/)")
    parser.add_argument("--model-dir", default="models", help="Model directory (default: models/)")
    parser.add_argument("--work-dir", default="work", help="Work directory (default: work/)")

    parser.add_argument("--model", default="large-v3", help="Whisper model name (default: large-v3)")
    parser.add_argument("--device", default="auto", choices=["cpu", "cuda", "auto"], help="Compute device")
    parser.add_argument("--compute-type", default=None, help="Compute type, e.g. int8 or float16")
    parser.add_argument("--language", default=None, help="Source language code; omit to auto-detect")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size")
    parser.add_argument("--no-vad", action="store_true", help="Disable VAD")
    parser.add_argument("--local-files-only", action="store_true", help="Use local model files only")

    parser.add_argument("--no-translate", action="store_true", help="Disable translation")
    parser.add_argument("--provider", default=None, help="Provider ID from config/providers.local.json")
    parser.add_argument("--language-profile", default=None, help="Language Profile ID")
    parser.add_argument("--api-provider", default=None, choices=["openai-compatible", "anthropic"], help="LLM API provider")
    parser.add_argument("--api-base", default=None, help="LLM API base URL")
    parser.add_argument("--api-key", default=None, help="LLM API key")
    parser.add_argument("--llm-model", default=None, help="LLM model name")
    parser.add_argument("--target-language", default="zh-CN", help="Translation target language")
    parser.add_argument("--translation-batch-size", type=int, default=20, help="Translation batch size")
    parser.add_argument("--translation-temperature", type=float, default=0.2, help="Translation temperature")
    parser.add_argument("--translation-mode", default="bilingual", choices=["bilingual", "translated"], help="Translation output mode")
    parser.add_argument("--context-window", type=int, default=3, help="Translation context window")
    parser.add_argument("--translation-prompt", default="", help="Custom translation prompt")
    parser.add_argument("--subtitle-formats", default=None, help="Subtitle output formats. ASS is reserved, e.g. srt,ass.")
    parser.add_argument("--ass-style-id", default=None, help="Reserved ASS style id. No .ass file is generated.")

    parser.add_argument("--max-retries", type=int, default=3, help="Maximum retries")
    parser.add_argument("--no-skip-completed", action="store_true", help="Reprocess completed tasks")
    parser.add_argument("--no-move-completed", action="store_true", help="Do not move completed inputs to archive/")

    parser.add_argument("--scan", action="store_true", help="Scan pending files without processing")
    parser.add_argument("--status", action="store_true", help="Show task status")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed tasks")
    parser.add_argument("--review", action="store_true", help="Show review summary from quality reports")
    parser.add_argument("--review-file", default=None, help="Show details for one quality report")

    args = parser.parse_args()

    os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(PROJECT_ROOT / ".cache" / "huggingface" / "hub"))

    raw_argv = [arg.split("=", 1)[0] for arg in sys.argv[1:]]

    def _explicit(*flags: str) -> bool:
        return any(flag in raw_argv for flag in flags)

    provider_config: dict = {}
    if args.provider is not None or not args.no_translate:
        try:
            from provider_store import resolve_provider_config
            provider_config = resolve_provider_config(args.provider)
            if provider_config:
                print(f"  [Provider] using config: {args.provider or '(active)'}")
        except Exception as exc:
            print(f"  [Provider] load failed: {exc}")

    lang_profile_config: dict = {}
    profile_id = args.language_profile or None
    try:
        from language_profile_store import resolve_language_profile_config
        lang_profile_config = resolve_language_profile_config(profile_id)
        if lang_profile_config:
            print(
                f"  [LangProfile] using config: "
                f"{lang_profile_config.get('profile_id', '?')} ({lang_profile_config.get('profile_name', '?')})"
            )
    except Exception as exc:
        print(f"  [LangProfile] load failed: {exc}")

    def _first(*values):
        """Return the first non-empty value."""
        for v in values:
            if v is not None and v != "":
                return v
        return ""

    effective_api_provider = _first(args.api_provider, provider_config.get("api_provider"), "openai-compatible")
    effective_api_base = _first(args.api_base, provider_config.get("api_base"), "")
    effective_api_key = _first(args.api_key, provider_config.get("api_key"), "")
    effective_llm_model = _first(args.llm_model, provider_config.get("llm_model"), "")

    lp_asr = lang_profile_config.get("asr", {})
    effective_model = _first(
        args.model if _explicit("--model") else None,
        lp_asr.get("whisper_model"),
        "large-v3"
    )
    effective_device = _first(
        args.device if _explicit("--device") else None,
        lp_asr.get("whisper_device"),
        "auto"
    )
    effective_compute_type = _first(
        args.compute_type if _explicit("--compute-type") else None,
        lp_asr.get("compute_type")
    )
    effective_language = _first(
        args.language if _explicit("--language") else None,
        lp_asr.get("language")
    )
    effective_vad = False if _explicit("--no-vad") else lp_asr.get("vad_filter", True)
    effective_beam_size = args.beam_size if _explicit("--beam-size") else lp_asr.get("beam_size", 5)

    effective_target_lang = _first(
        args.target_language if _explicit("--target-language") else None,
        lang_profile_config.get("target_language"),
        "zh-CN"
    )
    from subtitle_translate import build_effective_translation_prompt

    effective_translation_prompt = build_effective_translation_prompt(
        style_prompt=lang_profile_config.get("translation_style", ""),
        custom_prompt=args.translation_prompt,
        glossary=lang_profile_config.get("glossary", []),
    )
    lp_subtitle_style = lang_profile_config.get("subtitle_style", {})
    effective_subtitle_formats = normalize_subtitle_formats(
        args.subtitle_formats if _explicit("--subtitle-formats") else lp_subtitle_style.get("formats", ["srt"])
    )
    effective_ass_style_id = _first(
        args.ass_style_id if _explicit("--ass-style-id") else None,
        lp_subtitle_style.get("ass_style_id"),
        DEFAULT_ASS_STYLE_ID,
    )

    lang_profile_info = {
        "profile_id": lang_profile_config.get("profile_id", ""),
        "profile_name": lang_profile_config.get("profile_name", ""),
        "source_language": lang_profile_config.get("source_language", "auto"),
        "quality_thresholds": lang_profile_config.get("quality", {}),
        "translation_style": lang_profile_config.get("translation_style", ""),
        "glossary": lang_profile_config.get("glossary", []),
        "subtitle_style": lang_profile_config.get("subtitle_style", {}),
        "llm_stages": lang_profile_config.get("llm_stages", {}),
    }

    if args.api_key:
        os.environ["SUBTITLE_LLM_API_KEY"] = args.api_key
    elif effective_api_key:
        os.environ["SUBTITLE_LLM_API_KEY"] = effective_api_key

    config = BatchConfig(
        input_dir=Path(args.input).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        model_dir=Path(args.model_dir).resolve(),
        work_dir=Path(args.work_dir).resolve(),
        model=effective_model,
        device=effective_device,
        compute_type=effective_compute_type,
        language=effective_language,
        beam_size=effective_beam_size,
        vad_filter=effective_vad,
        local_files_only=args.local_files_only,
        translate=not args.no_translate,
        api_provider=effective_api_provider,
        api_base=effective_api_base,
        api_key=effective_api_key,
        llm_model=effective_llm_model,
        target_language=effective_target_lang,
        translation_prompt=effective_translation_prompt,
        translation_batch_size=args.translation_batch_size,
        translation_temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        context_window=args.context_window,
        subtitle_formats=effective_subtitle_formats,
        ass_style_id=effective_ass_style_id,
        subtitle_style=lp_subtitle_style,
        language_profile_id=lang_profile_info.get("profile_id", ""),
        language_profile_name=lang_profile_info.get("profile_name", ""),
        lang_profile_config=lang_profile_info,
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
        return _show_status()

    if args.retry_failed:
        return _retry_failed(pipeline)

    if args.review:
        return _show_review(config.output_dir)

    if args.review_file:
        return _show_review_detail(Path(args.review_file))

    api_key = effective_api_key or os.environ.get("SUBTITLE_LLM_API_KEY", "")
    if config.translate and not api_key:
        print("Warning: translation is enabled but no API key is configured.")
        print("Set a provider, pass --api-key, set SUBTITLE_LLM_API_KEY, or use --no-translate.")
        return 1

    result = pipeline.run()
    return 0 if result["failed"] == 0 else 1


def _show_status() -> int:
    """Show all task states."""
    if not DIR_WORK_STATES.exists():
        print("No task records found.")
        return 0

    state_files = sorted(DIR_WORK_STATES.glob("*.state.json"))
    if not state_files:
        print("No task records found.")
        return 0

    print(f"\nTask status ({len(state_files)}):\n")
    print(f"  {'file':<40} {'status':<12} {'stage':<20} {'retry'}")
    print(f"  {'-' * 40} {'-' * 12} {'-' * 20} {'-' * 6}")

    for state_file in state_files:
        task = TaskState.load(state_file)
        if task is None:
            continue
        retry = f"{task.retry_count}/{task.max_retries}" if task.retry_count > 0 else "-"
        print(f"  {task.file:<40} {task.status:<12} {task.stage:<20} {retry}")
        if task.error:
            print(f"    error: {task.error[:100]}")

    print()
    return 0


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
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


def _show_review(output_root: Path = DIR_OUTPUT) -> int:
    """Show a summary of quality reports that need manual review."""
    reports_dir = plan_pipeline_outputs(output_root, "", "", "", "bilingual").reports_dir
    if not reports_dir.exists():
        _safe_console_print("No quality reports found.")
        return 0

    report_files = sorted(reports_dir.glob("*.quality_report.json"))
    if not report_files:
        _safe_console_print("No quality reports found.")
        return 0

    _safe_console_print(f"\n{'=' * 70}")
    _safe_console_print(f"  Review summary - {len(report_files)} report(s)")
    _safe_console_print(f"{'=' * 70}\n")

    total_issues = 0
    total_errors = 0
    total_warnings = 0

    for report_file in report_files:
        try:
            data = read_json(report_file)
        except (OSError, json.JSONDecodeError):
            continue

        status = data.get("status", "?")
        summary = data.get("summary", {})
        issues = data.get("issues", [])
        if not issues:
            continue

        status_icon = {"pass": "OK", "warning": "WARN", "fail": "FAIL"}.get(status, "?")
        video_name = report_file.stem.replace(".quality_report", "")
        _safe_console_print(f"  {status_icon} {video_name}")
        _safe_console_print(
            f"    status: {status} | issues: {summary.get('total_issues', 0)} "
            f"(errors: {summary.get('errors', 0)}, warnings: {summary.get('warnings', 0)})"
        )

        severity_order = {"error": 0, "warning": 1, "info": 2}
        sorted_issues = sorted(issues, key=lambda item: severity_order.get(item.get("severity", "info"), 99))
        for issue in sorted_issues[:10]:
            icon = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(issue.get("severity"), "?")
            idx = issue.get("index", 0)
            idx_str = f"#{idx}" if idx > 0 else "global"
            snippet = issue.get("snippet", "")[:60]
            _safe_console_print(f"    {icon} {idx_str} [{issue.get('type', '?')}] {issue.get('text', '')[:80]}")
            if snippet and snippet != "(empty)":
                _safe_console_print(f"       content: {snippet}")

        if len(sorted_issues) > 10:
            _safe_console_print(f"    ... {len(sorted_issues) - 10} more issue(s); use --review-file for details")

        total_issues += summary.get("total_issues", 0)
        total_errors += summary.get("errors", 0)
        total_warnings += summary.get("warnings", 0)
        _safe_console_print()

    _safe_console_print(f"{'=' * 70}")
    _safe_console_print(f"  Total: {total_issues} issue(s) ({total_errors} errors, {total_warnings} warnings)")
    _safe_console_print(f"  Reports: {reports_dir}")
    _safe_console_print(f"  Review subtitles: {reports_dir}/*.review_needed.srt")
    _safe_console_print(f"{'=' * 70}")
    _safe_console_print("\nTip: use --review-file <report path> for full details.\n")

    return 0 if total_errors == 0 else 1

def _show_review_detail(report_path: Path) -> int:
    """Show details for one quality report."""
    if not report_path.exists():
        _safe_console_print(f"Report not found: {report_path}")
        return 1

    try:
        data = read_json(report_path)
    except (OSError, json.JSONDecodeError) as exc:
        _safe_console_print(f"Could not read report: {exc}")
        return 1

    status = data.get("status", "?")
    summary = data.get("summary", {})
    issues = data.get("issues", [])

    _safe_console_print(f"\n{'=' * 70}")
    _safe_console_print(f"  Quality report: {report_path.name}")
    _safe_console_print(f"{'=' * 70}")
    _safe_console_print(f"  status: {status}")
    _safe_console_print(f"  total entries: {data.get('total_entries', 0)}")
    _safe_console_print(f"  source: {data.get('source_srt', '')}")
    _safe_console_print(f"  translated: {data.get('translated_srt', '')}")
    _safe_console_print(
        f"  issues: {summary.get('total_issues', 0)} "
        f"(errors: {summary.get('errors', 0)}, "
        f"warnings: {summary.get('warnings', 0)}, "
        f"info: {summary.get('info', 0)})"
    )

    issue_types = summary.get("issue_types", {})
    if issue_types:
        _safe_console_print("\n  issue types:")
        for issue_type, count in sorted(issue_types.items(), key=lambda item: -item[1]):
            _safe_console_print(f"    - {issue_type}: {count}")

    if not issues:
        _safe_console_print("\n  [OK] no issues")
    else:
        _safe_console_print(f"\n  all issues ({len(issues)}):")
        severity_order = {"error": 0, "warning": 1, "info": 2}
        sorted_issues = sorted(issues, key=lambda item: severity_order.get(item.get("severity", "info"), 99))
        for issue in sorted_issues:
            icon = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(issue.get("severity"), "?")
            idx = issue.get("index", 0)
            idx_str = f"#{idx}" if idx > 0 else "global"
            _safe_console_print(f"\n    {icon} {idx_str} [{issue.get('type', '?')}]")
            _safe_console_print(f"       description: {issue.get('text', '')}")
            snippet = issue.get("snippet", "")
            if snippet and snippet != "(empty)":
                _safe_console_print(f"       content: {snippet}")

    _safe_console_print()
    return 0 if status != "fail" else 1

if __name__ == "__main__":
    raise SystemExit(main())
