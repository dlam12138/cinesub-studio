from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from encoding_utils import read_json, write_json
from output_paths import plan_pipeline_outputs


class TaskStage:
    PENDING = "pending"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    QUALITY_CHECKING = "quality_checking"
    COMPLETED = "completed"
    FAILED = "failed"


_state_root_provider: Callable[[], Path] = lambda: Path("work") / "states"


def set_state_root_provider(provider: Callable[[], Path]) -> None:
    global _state_root_provider
    _state_root_provider = provider


def is_valid_output_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


@dataclass
class TaskState:
    file: str
    input_path: str
    stage: str = TaskStage.PENDING
    status: str = "pending"
    created_at: float = 0.0
    updated_at: float = 0.0
    audio_path: str = ""
    language_detection: dict | None = None
    source_srt: str = ""
    translated_srt: str = ""
    bilingual_srt: str = ""
    quality_report: str = ""
    segment_asr_routing_report: str = ""
    segment_asr_routing_status: str = ""
    segment_asr_routing_message: str = ""
    error: str = ""
    error_stage: str = ""
    retry_count: int = 0
    max_retries: int = 3
    output_dir: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState":
        fields = cls.__dataclass_fields__
        return cls(**{key: data[key] for key in fields if key in data})

    def state_path(self) -> Path:
        return _state_root_provider() / f"{Path(self.file).stem}.state.json"

    def save(self) -> None:
        self.updated_at = time.time()
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, self.to_dict())

    @classmethod
    def load(cls, state_path: Path) -> Optional["TaskState"]:
        if not state_path.exists():
            return None
        try:
            data = read_json(state_path)
            return cls.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None


@dataclass
class RetryPlan:
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


def prepare_retry_failed_tasks(state_files: list[Path]) -> RetryPlan:
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


def required_final_outputs(task: TaskState, config) -> list[Path]:
    input_path = Path(task.input_path or task.file)
    outputs = plan_pipeline_outputs(
        output_root=config.output_dir,
        stem=input_path.stem,
        model=config.model,
        target_language=config.target_language,
        translation_mode=config.translation_mode,
    )
    required = [outputs.source_srt]
    if config.translate:
        required.extend([outputs.translation_output, outputs.quality_report])
    return required


def completed_outputs_valid(task: TaskState, config) -> bool:
    return task.status == "completed" and all(
        is_valid_output_file(path) for path in required_final_outputs(task, config)
    )


def recovery_state(raw: dict, effective_status: str) -> dict:
    if effective_status == "failed":
        return _recovery("retry_failed", True)
    if effective_status == "stale":
        return _recovery("stale_running_warning", False)
    if effective_status == "completed":
        return _recovery(
            "skip_completed" if raw_completed_outputs_valid(raw) else "not_recoverable", False
        )
    if effective_status == "pending" and raw_has_reusable_outputs(raw):
        return _recovery("reuse_outputs", True)
    return _recovery("none", False)


def _recovery(action: str, recoverable: bool) -> dict:
    labels = {
        "none": "无恢复操作",
        "retry_failed": "可重试失败任务",
        "skip_completed": "已完成且产物有效，将跳过",
        "reuse_outputs": "可复用已有中间产物",
        "stale_running_warning": "可能中断，需人工确认",
        "not_recoverable": "状态与产物不一致",
    }
    return {"recovery_action": action, "recoverable": recoverable, "recovery_label": labels[action]}


def raw_state_output_paths(raw: dict) -> list[str]:
    paths = [raw.get("source_srt", "")]
    paths.extend(
        path for path in (raw.get("translated_srt", ""), raw.get("bilingual_srt", "")) if path
    )
    if raw.get("quality_report"):
        paths.append(raw["quality_report"])
    return [path for path in paths if path]


def raw_completed_outputs_valid(raw: dict) -> bool:
    paths = raw_state_output_paths(raw)
    return bool(paths) and all(is_valid_output_file(Path(path)) for path in paths)


def raw_has_reusable_outputs(raw: dict) -> bool:
    paths = [
        raw.get("audio_path", ""), raw.get("source_srt", ""), raw.get("translated_srt", ""),
        raw.get("bilingual_srt", ""), raw.get("quality_report", ""),
    ]
    return any(is_valid_output_file(Path(path)) for path in paths if path)
