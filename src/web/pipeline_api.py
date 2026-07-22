from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from process_env import build_child_process_env, redact_project_path
from runtime_paths import resolve_runtime_paths
from subtitle_model import ASS_RESERVED_MESSAGE, DEFAULT_ASS_STYLE_ID, normalize_subtitle_formats
from task_state import (
    is_valid_output_file,
    plan_retry_failed_tasks,
    recovery_state as shared_recovery_state,
)
from pipeline_reliability import (
    PipelineRunLock,
    build_pipeline_plan as build_read_only_pipeline_plan,
    canonical_hash,
    local_provider_preflight,
    process_identity_matches,
    read_run_record,
    sanitize_stem,
    windows_process_creation_filetime,
    write_run_record,
)
from stage_event_log import sanitize_event_text


PATHS = resolve_runtime_paths()
PROJECT_ROOT = PATHS.project_root
APP_ROOT = PATHS.app_root
SRC_ROOT = PATHS.src_root
WORK_DIR = PROJECT_ROOT / "work"
PIPELINE_STATES_DIR = WORK_DIR / "states"
PIPELINE_LOG = PROJECT_ROOT / "logs" / "pipeline.log"
OUTPUT_DIR = PROJECT_ROOT / "output"
PIPELINE_RUN_RECORD = WORK_DIR / "pipeline_run.json"
PIPELINE_RUN_LOCK = WORK_DIR / "pipeline_run.lock"

ARTIFACT_TYPES = {
    "source",
    "translated",
    "bilingual",
    "quality_report",
    "review_needed",
    "semantic_review_report",
    "asr_review_report",
}

PIPELINE_TASK: dict[str, Any] = {
    "running": False,
    "pid": None,
    "action": "",
    "started_at": 0,
    "finished_at": 0,
    "returncode": None,
    "error": "",
    "run_id": "",
}
PIPELINE_TASK_LOCK = threading.Lock()

STAGE_PROGRESS = {
    "pending": 0,
    "extracting_audio": 20,
    "transcribing": 40,
    "translating": 60,
    "quality_checking": 80,
    "completed": 100,
    "failed": 100,
}

STAGE_LABELS = {
    "pending": "等待开始",
    "extracting_audio": "提取音频",
    "transcribing": "转写",
    "translating": "翻译",
    "quality_checking": "质检",
    "completed": "完成",
    "failed": "失败",
}

STATUS_LABELS = {
    "pending": "等待中",
    "running": "处理中",
    "completed": "已完成",
    "failed": "失败",
    "stale": "可能中断",
}


def get_pipeline_task() -> dict:
    with PIPELINE_TASK_LOCK:
        memory = dict(PIPELINE_TASK)
    record = read_run_record(PIPELINE_RUN_RECORD)
    if record and record.get("status") in {"preparing", "running"}:
        if record.get("status") == "preparing" and memory.get("running"):
            return {
                **memory,
                "action": record.get("action", memory.get("action", "")),
                "started_at": record.get("started_at", memory.get("started_at", 0)),
                "run_id": record.get("run_id", memory.get("run_id", "")),
            }
        alive = process_identity_matches(
            int(record.get("worker_pid") or 0),
            int(record.get("worker_creation_filetime") or 0),
        )
        if alive:
            return {
                **memory,
                "running": True,
                "pid": record.get("worker_pid"),
                "action": record.get("action", ""),
                "started_at": record.get("started_at", 0),
                "run_id": record.get("run_id", ""),
            }
        if record.get("status") != "stale":
            record["status"] = "stale"
            record["finished_at"] = time.time()
            write_run_record(PIPELINE_RUN_RECORD, record)
    return memory


def _safe_artifacts(raw: dict) -> dict:
    artifacts = pipeline_artifacts_for_state(raw)
    return {
        kind: {key: value for key, value in metadata.items() if key != "path"}
        for kind, metadata in artifacts.items()
    }


def _error_category(raw: dict) -> str:
    value = str(raw.get("error_category") or "").strip()
    if value:
        return sanitize_stem(value)[:80]
    error = str(raw.get("error") or "")
    return "pipeline_stage_error" if error else ""


def _error_summary(raw: dict) -> str:
    return sanitize_event_text(raw.get("error") or "")[:300]


def pipeline_progress() -> dict:
    task_info = get_pipeline_task()
    background_running = bool(task_info.get("running"))
    raw_tasks = _load_pipeline_state_files()
    items: list[dict] = []

    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "stale": 0}
    progress_sum = 0
    stale_running = False
    recoverable_failed_count = 0
    stale_running_count = 0

    for raw in raw_tasks:
        status = str(raw.get("status") or "pending")
        stage = str(raw.get("stage") or "pending")
        effective_status = status
        warning = ""
        if status == "running" and not background_running:
            effective_status = "stale"
            warning = "后台任务已结束，但状态文件仍停在运行中；建议查看日志后重试失败。"
            stale_running = True
            stale_running_count += 1

        recovery = _recovery_state(raw, effective_status)
        if recovery["recovery_action"] == "retry_failed":
            recoverable_failed_count += 1

        percent = 100 if effective_status in {"completed", "failed", "stale"} else STAGE_PROGRESS.get(stage, 0)
        progress_sum += percent
        counts[effective_status] = counts.get(effective_status, 0) + 1

        items.append({
            "task_id": _task_id_for_state(raw),
            "file": Path(str(raw.get("file") or "")).name,
            "display_name": Path(str(raw.get("file") or "")).name,
            "relative_input_path": str(
                raw.get("original_relative_path") or Path(str(raw.get("file") or "")).name
            ).replace("\\", "/"),
            "input_location": raw.get("input_location", "active"),
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "status": effective_status,
            "raw_status": status,
            "status_label": STATUS_LABELS.get(effective_status, effective_status),
            "progress": percent,
            "retry_count": raw.get("retry_count", 0),
            "max_retries": raw.get("max_retries", 0),
            "error_category": _error_category(raw),
            "error_summary": _error_summary(raw),
            "error_stage": raw.get("error_stage", ""),
            "updated_at": raw.get("updated_at", 0),
            "warning": warning,
            "recoverable": recovery["recoverable"],
            "recovery_action": recovery["recovery_action"],
            "recovery_label": recovery["recovery_label"],
            "asr_mode": raw.get("asr_mode", ""),
            "language": raw.get("language", ""),
            "language_detection": _language_detection_summary(raw.get("language_detection")),
            "asr_review_summary": raw.get("asr_review_summary") or {},
            "target_language": _target_language_from_state(raw),
            "quality_summary": _quality_summary(raw),
            "artifacts": _safe_artifacts(raw),
        })

    total = len(items)
    overall = round(progress_sum / total) if total else 0
    current = next((item for item in items if item["status"] == "running"), None)
    if current is None:
        current = next((item for item in items if item["status"] == "stale"), None)

    return {
        "ok": True,
        "total": total,
        "overall_progress": overall,
        "counts": counts,
        "current": current,
        "tasks": items,
        "task": {
            key: value for key, value in task_info.items()
            if key not in {"error"}
        } | ({"error_summary": sanitize_event_text(task_info.get("error", ""))[:300]} if task_info.get("error") else {}),
        "run": {
            key: value for key, value in read_run_record(PIPELINE_RUN_RECORD).items()
            if key in {
                "schema_version", "run_id", "action", "status", "task_ids",
                "current_task_id", "current_stage", "started_at", "updated_at",
                "finished_at", "counts", "failure_stage_counts",
            }
        },
        "stale_running": stale_running,
        "stale_running_count": stale_running_count,
        "recoverable_failed_count": recoverable_failed_count,
        "can_retry_failed": recoverable_failed_count > 0,
    }


def run_pipeline_command(action: str, timeout: int = 30, input_dir: str = "") -> dict:
    command = [
        sys.executable,
        "-B",
        str(SRC_ROOT / "pipeline" / "batch_worker.py"),
        f"--{action}",
    ]
    if input_dir:
        command += ["--input", input_dir]

    env = _pipeline_env()
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        payload = {
            "ok": result.returncode == 0,
            "command": action,
            "output": _sanitize_process_summary(result.stdout),
            "error": _sanitize_process_summary(result.stderr),
            "returncode": result.returncode,
        }
        if action == "review":
            _classify_review_result(payload)
        if action == "status":
            payload["progress"] = pipeline_progress()
        return payload
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": action, "error": f"命令超时（{timeout}s）"}
    except FileNotFoundError:
        return {"ok": False, "command": action, "error": "Python 解释器未找到。"}
    except Exception as exc:
        return {"ok": False, "command": action, "error": _sanitize_process_summary(exc)}


def _sanitize_process_summary(value: object, *, max_lines: int = 80) -> str:
    lines = str(value or "").splitlines()[-max_lines:]
    return "\n".join(sanitize_event_text(line) for line in lines)[:8000]


def _batch_config_from_command(command: list[str]):
    from batch_worker import BatchConfig
    from pipeline_cli import build_pipeline_parser
    from pipeline_config import resolve_cli_config

    argv = command[3:]
    args = build_pipeline_parser().parse_args(argv)
    raw_argv = [value.split("=", 1)[0] for value in argv]
    effective, _messages = resolve_cli_config(args, raw_argv)
    return BatchConfig(
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


def scan_pipeline(input_dir: str = "") -> dict:
    command = _build_background_command(
        action="run",
        provider_id="",
        language_profile_id="",
        input_dir=input_dir,
        model="small",
        device="auto",
        compute_type="",
        translate_enabled=True,
        asr_mode="auto",
    )
    config = _batch_config_from_command(command)
    plan = build_read_only_pipeline_plan(
        config,
        state_dir=PIPELINE_STATES_DIR,
        video_extensions={
            ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v",
            ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".wav",
        },
    )
    return plan.to_dict()


def start_pipeline_background(
    *,
    action: str,
    provider_id: str = "",
    language_profile_id: str = "",
    input_dir: str = "",
    model: str = "small",
    device: str = "auto",
    compute_type: str = "",
    translate_enabled: bool = True,
    asr_mode: str,
    language: str = "",
    hf_endpoint: str = "",
    local_files_only: bool = False,
    quality_preset: str = "",
    word_timestamps: object = None,
    resegment_subtitles: object = None,
    asr_retry_mode: object = None,
    asr_hotword_prompt: str = "",
    translation_reliability_mode: str | None = None,
    translation_max_extra_requests: int | None = None,
    translation_strategy_mode: str | None = None,
    translation_scene_gap_seconds: float | None = None,
    subtitle_formats: list[str] | str | None = None,
    ass_style_id: str = "",
) -> tuple[dict, int]:
    subtitle_formats_list = normalize_subtitle_formats(subtitle_formats)
    command = _build_background_command(
        action=action,
        provider_id=provider_id,
        language_profile_id=language_profile_id,
        input_dir=input_dir,
        model=model,
        device=device,
        compute_type=compute_type,
        translate_enabled=translate_enabled,
        asr_mode=asr_mode,
        language=language,
        local_files_only=local_files_only,
        quality_preset=quality_preset,
        word_timestamps=word_timestamps,
        resegment_subtitles=resegment_subtitles,
        asr_retry_mode=asr_retry_mode,
        asr_hotword_prompt=asr_hotword_prompt,
        translation_reliability_mode=translation_reliability_mode,
        translation_max_extra_requests=translation_max_extra_requests,
        translation_strategy_mode=translation_strategy_mode,
        translation_scene_gap_seconds=translation_scene_gap_seconds,
        subtitle_formats=subtitle_formats_list,
        ass_style_id=ass_style_id,
    )
    config = _batch_config_from_command(command)
    run_lock = PipelineRunLock(PIPELINE_RUN_LOCK)
    if not run_lock.acquire():
        return {"ok": False, "error": "已有流水线进程持有运行锁。", "code": "pipeline_busy"}, 409
    worker_lease = PipelineRunLock(PIPELINE_RUN_LOCK, offset=1)
    if not worker_lease.acquire():
        run_lock.release()
        return {"ok": False, "error": "已有 worker 持有运行锁。", "code": "pipeline_busy"}, 409
    if action == "run":
        plan = build_read_only_pipeline_plan(
            config,
            state_dir=PIPELINE_STATES_DIR,
            video_extensions={
                ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v",
                ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".wav",
            },
        )
        blockers = plan.blockers
        task_ids = [item.task_id for item in plan.tasks]
        plan_fingerprint = plan.plan_fingerprint
        config_hash = plan.effective_config_hash
    else:
        blockers = local_provider_preflight(config)
        retry_plan = plan_retry_failed_tasks(sorted(PIPELINE_STATES_DIR.glob("*.state.json")))
        task_ids = retry_plan.selected_task_ids
        plan_fingerprint = canonical_hash(task_ids)
        config_hash = canonical_hash(config.asr_signature_payload())
    if blockers:
        worker_lease.release()
        run_lock.release()
        return {
            "ok": False,
            "error": "Pipeline preflight failed.",
            "blockers": [blocker.__dict__ for blocker in blockers],
        }, 409

    run_id = uuid.uuid4().hex
    started_at = time.time()
    write_run_record(PIPELINE_RUN_RECORD, {
        "schema_version": 1,
        "run_id": run_id,
        "action": action,
        "status": "preparing",
        "server_pid": os.getpid(),
        "worker_pid": 0,
        "worker_creation_filetime": 0,
        "plan_fingerprint": plan_fingerprint,
        "effective_config_hash": config_hash,
        "task_ids": task_ids,
        "current_task_id": "",
        "current_stage": "",
        "started_at": started_at,
        "finished_at": 0,
        "counts": {},
        "failure_stage_counts": {},
    })
    with PIPELINE_TASK_LOCK:
        if PIPELINE_TASK["running"]:
            worker_lease.release()
            run_lock.release()
            return {"ok": False, "error": "已有流水线任务正在运行，请等待完成。"}, 409
        PIPELINE_TASK.update({
            "running": True,
            "pid": None,
            "action": action,
            "started_at": started_at,
            "finished_at": 0,
            "returncode": None,
            "error": "",
            "run_id": run_id,
        })

    thread = threading.Thread(
        target=run_pipeline_background,
        kwargs={
            "action": action,
            "provider_id": provider_id,
            "language_profile_id": language_profile_id,
            "input_dir": input_dir,
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "translate_enabled": translate_enabled,
            "asr_mode": asr_mode,
            "language": language,
            "hf_endpoint": hf_endpoint,
            "local_files_only": local_files_only,
            "quality_preset": quality_preset,
            "word_timestamps": word_timestamps,
            "resegment_subtitles": resegment_subtitles,
            "asr_retry_mode": asr_retry_mode,
            "asr_hotword_prompt": asr_hotword_prompt,
            "translation_reliability_mode": translation_reliability_mode,
            "translation_max_extra_requests": translation_max_extra_requests,
            "translation_strategy_mode": translation_strategy_mode,
            "translation_scene_gap_seconds": translation_scene_gap_seconds,
            "subtitle_formats": subtitle_formats,
            "ass_style_id": ass_style_id,
            "_command": command,
            "_run_lock": run_lock,
            "_worker_lease": worker_lease,
            "_run_id": run_id,
            "_plan_fingerprint": plan_fingerprint,
        },
        daemon=True,
    )
    thread.start()
    label = "input 目录处理" if action == "run" else "retry-failed"
    return {"ok": True, "message": f"{label} 已启动。", "run_id": run_id}, 202


def run_pipeline_background(
    action: str,
    provider_id: str = "",
    language_profile_id: str = "",
    input_dir: str = "",
    model: str = "small",
    device: str = "auto",
    compute_type: str = "",
    translate_enabled: bool = True,
    asr_mode: str = "",
    language: str = "",
    hf_endpoint: str = "",
    local_files_only: bool = False,
    quality_preset: str = "",
    word_timestamps: object = None,
    resegment_subtitles: object = None,
    asr_retry_mode: object = None,
    asr_hotword_prompt: str = "",
    translation_reliability_mode: str | None = None,
    translation_max_extra_requests: int | None = None,
    translation_strategy_mode: str | None = None,
    translation_scene_gap_seconds: float | None = None,
    subtitle_formats: list[str] | str | None = None,
    ass_style_id: str = "",
    _command: list[str] | None = None,
    _run_lock: PipelineRunLock | None = None,
    _worker_lease: PipelineRunLock | None = None,
    _run_id: str = "",
    _plan_fingerprint: str = "",
) -> None:
    subtitle_formats_list = normalize_subtitle_formats(subtitle_formats)
    ass_style_id = ass_style_id or DEFAULT_ASS_STYLE_ID
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    command = _command or _build_background_command(
        action=action,
        provider_id=provider_id,
        language_profile_id=language_profile_id,
        input_dir=input_dir,
        model=model,
        device=device,
        compute_type=compute_type,
        translate_enabled=translate_enabled,
        asr_mode=asr_mode,
        language=language,
        local_files_only=local_files_only,
        quality_preset=quality_preset,
        word_timestamps=word_timestamps,
        resegment_subtitles=resegment_subtitles,
        asr_retry_mode=asr_retry_mode,
        asr_hotword_prompt=asr_hotword_prompt,
        translation_reliability_mode=translation_reliability_mode,
        translation_max_extra_requests=translation_max_extra_requests,
        translation_strategy_mode=translation_strategy_mode,
        translation_scene_gap_seconds=translation_scene_gap_seconds,
        subtitle_formats=subtitle_formats_list,
        ass_style_id=ass_style_id,
    )
    env = _pipeline_env()
    env["CINESUB_PIPELINE_RUN_ID"] = _run_id
    env["CINESUB_PIPELINE_SERVER_PID"] = str(os.getpid())
    env["CINESUB_PIPELINE_EXPECTED_PLAN"] = _plan_fingerprint
    handoff_ack = WORK_DIR / f".pipeline-lock-handoff-{_run_id}.ack"
    if _run_lock is not None:
        env["CINESUB_PIPELINE_LOCK_PATH"] = str(PIPELINE_RUN_LOCK)
        env["CINESUB_PIPELINE_LOCK_ACK"] = str(handoff_ack)
    if hf_endpoint:
        env["HF_ENDPOINT"] = hf_endpoint

    action_label = {"run": "完整流水线", "retry-failed": "重试失败任务"}.get(action, action)
    _append_pipeline_log_header(
        action_label=action_label,
        command=command,
        provider_id=provider_id,
        language_profile_id=language_profile_id,
        hf_endpoint=hf_endpoint,
        local_files_only=local_files_only,
        subtitle_formats=subtitle_formats_list,
    )

    returncode = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        record = read_run_record(PIPELINE_RUN_RECORD)
        write_run_record(PIPELINE_RUN_RECORD, {
            **record,
            "worker_pid": process.pid,
            "worker_creation_filetime": windows_process_creation_filetime(process.pid),
        })
        if _worker_lease is not None:
            _worker_lease.release()
            _worker_lease = None
        deadline = time.monotonic() + 10.0
        while not handoff_ack.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not handoff_ack.exists():
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("worker did not acquire the pipeline lease")
        if _run_lock is not None:
            _run_lock.release()
            _run_lock = None
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["pid"] = process.pid

        assert process.stdout is not None
        with PIPELINE_LOG.open("a", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(_clean_log_line(line))
                log.flush()

        returncode = process.wait()
        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with PIPELINE_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n[{action_label}] 完成于 {finished_at}, returncode={returncode}\n")
    except Exception as exc:
        with PIPELINE_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n[{action_label}] 异常: {_sanitize_process_summary(exc)}\n")
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["error"] = str(exc)
    finally:
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["running"] = False
            PIPELINE_TASK["pid"] = None
            PIPELINE_TASK["finished_at"] = time.time()
            PIPELINE_TASK["returncode"] = returncode
        if _run_lock is not None:
            _run_lock.release()
        if _worker_lease is not None:
            _worker_lease.release()
        try:
            handoff_ack.unlink()
        except OSError:
            pass


def read_pipeline_log() -> dict:
    if not PIPELINE_LOG.exists():
        return {"ok": True, "lines": [], "text": ""}
    try:
        text = PIPELINE_LOG.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc), "lines": [], "text": ""}
    lines = [_clean_log_line(line) for line in text.splitlines()[-200:]]
    return {"ok": True, "lines": lines, "text": "\n".join(lines)}


def _load_pipeline_state_files() -> list[dict]:
    if not PIPELINE_STATES_DIR.exists():
        return []
    tasks: list[dict] = []
    for state_path in sorted(PIPELINE_STATES_DIR.glob("*.state.json")):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_state_path"] = str(state_path)
        tasks.append(data)
    return tasks


def resolve_pipeline_artifact(task_id: str, artifact_type: str) -> tuple[Path | None, str]:
    """Resolve a downloadable pipeline artifact from state metadata only."""
    task_id = _clean_task_id(task_id)
    artifact_type = str(artifact_type or "").strip()
    if artifact_type not in ARTIFACT_TYPES:
        return None, "Unknown artifact type"
    if not task_id:
        return None, "Invalid task id"

    state_path = PIPELINE_STATES_DIR / f"{task_id}.state.json"
    try:
        resolved_state = state_path.resolve()
        if not resolved_state.is_relative_to(PIPELINE_STATES_DIR.resolve()):
            return None, "Invalid task id"
    except OSError:
        return None, "Invalid task id"
    if not state_path.is_file():
        return None, "Task state not found"

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "Task state could not be read"
    raw["_state_path"] = str(state_path)

    artifact = pipeline_artifacts_for_state(raw).get(artifact_type)
    if not artifact or not artifact.get("downloadable"):
        return None, "Artifact is not downloadable"

    path = Path(str(artifact.get("path") or "")).resolve()
    if not _is_downloadable_output(path) or not _is_valid_file(str(path)):
        return None, "Artifact not found"
    return path, ""


def pipeline_artifacts_for_state(raw: dict) -> dict:
    task_id = _task_id_for_state(raw)
    paths = _artifact_paths(raw)
    return {
        kind: _artifact_metadata(task_id, kind, path)
        for kind, path in paths.items()
    }


def _artifact_paths(raw: dict) -> dict[str, str]:
    paths = {
        "source": str(raw.get("source_srt") or ""),
        "translated": str(raw.get("translated_srt") or ""),
        "bilingual": str(raw.get("bilingual_srt") or ""),
        "quality_report": str(raw.get("quality_report") or ""),
        "review_needed": "",
        "semantic_review_report": str(raw.get("semantic_review_report") or ""),
        "asr_review_report": str(raw.get("asr_review_report") or ""),
    }
    quality_report = paths["quality_report"]
    if quality_report:
        report_path = Path(quality_report)
        if report_path.name.endswith(".quality_report.json"):
            paths["review_needed"] = str(
                report_path.with_name(report_path.name.replace(".quality_report.json", ".review_needed.srt"))
            )
    return paths


def _artifact_metadata(task_id: str, kind: str, path_text: str) -> dict:
    metadata = {
        "type": kind,
        "label": _artifact_label(kind),
        "path": path_text,
        "exists": False,
        "size": 0,
        "display_size": "",
        "downloadable": False,
        "download_url": "",
    }
    if not path_text:
        return metadata

    try:
        path = Path(path_text).resolve()
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
    except OSError:
        return metadata

    downloadable = exists and size > 0 and _is_downloadable_output(path)
    metadata.update({
        "path": str(path),
        "exists": exists,
        "size": size,
        "display_size": _format_bytes(size) if exists else "",
        "downloadable": downloadable,
        "download_url": _artifact_download_url(task_id, kind) if downloadable else "",
    })
    return metadata


def _artifact_label(kind: str) -> str:
    labels = {
        "source": "Source SRT",
        "translated": "Translated SRT",
        "bilingual": "Bilingual SRT",
        "quality_report": "Quality report",
        "review_needed": "Review SRT",
        "semantic_review_report": "Semantic review report",
        "asr_review_report": "ASR review report",
    }
    return labels.get(kind, kind)


def _artifact_download_url(task_id: str, kind: str) -> str:
    return f"/api/pipeline/artifact?task={quote(task_id)}&artifact={quote(kind)}"


def _task_id_for_state(raw: dict) -> str:
    return _clean_task_id(str(raw.get("task_id") or ""))


def _clean_task_id(task_id: str) -> str:
    value = str(task_id or "").strip()
    if not value or value in {".", ".."}:
        return ""
    if any(sep in value for sep in ("/", "\\")):
        return ""
    if Path(value).name != value:
        return ""
    return value


def _language_detection_summary(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        "asr_mode": value.get("asr_mode", ""),
        "source_language": value.get("source_language", ""),
        "language_probability": value.get("language_probability"),
        "forced_language": value.get("forced_language"),
        "distinct_languages": value.get("distinct_languages", []),
        "block_count": value.get("block_count", 0),
        "manual_review_count": value.get("manual_review_count", 0),
        "asr_review_summary": value.get("asr_review_summary", {}),
        "model": value.get("model", ""),
        "device": value.get("device", ""),
        "compute_type": value.get("compute_type", ""),
        "language_profile": value.get("language_profile", ""),
        "language_profile_name": value.get("language_profile_name", ""),
    }


def _target_language_from_state(raw: dict) -> str:
    for key in ("translated_srt", "bilingual_srt"):
        path_text = str(raw.get(key) or "")
        match = re.search(r"\.(?:translated|bilingual)\.([A-Za-z]{2}(?:-[A-Za-z0-9]+)?)\.srt$", path_text)
        if match:
            return match.group(1)
    return ""


def _quality_summary(raw: dict) -> dict:
    report_path = raw.get("quality_report", "")
    if not report_path or not _is_valid_file(str(report_path)):
        return {}
    try:
        data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
    return {
        "status": data.get("status", ""),
        "total_issues": summary.get("total_issues", 0),
        "errors": summary.get("errors", 0),
        "warnings": summary.get("warnings", 0),
        "issue_types": summary.get("issue_types", {}),
    }


def _is_downloadable_output(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(OUTPUT_DIR.resolve())
    except OSError:
        return False


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _classify_review_result(payload: dict) -> None:
    output = str(payload.get("output") or "")
    returncode = payload.get("returncode")
    valid_summary = (
        returncode == 1
        and "Review summary" in output
        and ("Reports:" in output or "Review subtitles:" in output)
    )
    if valid_summary:
        payload["ok"] = True
        payload["review_status"] = "issues_found"
    elif returncode == 0:
        payload["review_status"] = "ok"
    else:
        payload["review_status"] = "failed"


def _recovery_state(raw: dict, effective_status: str) -> dict:
    return shared_recovery_state(raw, effective_status)


def _is_valid_file(path: str) -> bool:
    return bool(path) and is_valid_output_file(Path(path))


def _build_background_command(
    *,
    action: str,
    provider_id: str,
    language_profile_id: str,
    input_dir: str,
    model: str,
    device: str,
    compute_type: str,
    translate_enabled: bool,
    asr_mode: str = "",
    language: str = "",
    local_files_only: bool = False,
    quality_preset: str = "",
    word_timestamps: object = None,
    resegment_subtitles: object = None,
    asr_retry_mode: object = None,
    asr_hotword_prompt: str = "",
    subtitle_formats: list[str] | None = None,
    ass_style_id: str = "",
    translation_reliability_mode: str | None = None,
    translation_max_extra_requests: int | None = None,
    translation_strategy_mode: str | None = None,
    translation_scene_gap_seconds: float | None = None,
) -> list[str]:
    subtitle_formats = normalize_subtitle_formats(subtitle_formats)
    ass_style_id = ass_style_id or DEFAULT_ASS_STYLE_ID
    if action == "run":
        command = [
            sys.executable,
            "-B",
            str(SRC_ROOT / "pipeline" / "batch_worker.py"),
            "--input",
            input_dir if input_dir else str(PROJECT_ROOT / "input"),
        ]
    else:
        command = [
            sys.executable,
            "-B",
            str(SRC_ROOT / "pipeline" / "batch_worker.py"),
            f"--{action}",
        ]

    if not provider_id:
        provider_id = _active_provider_id()
    if not language_profile_id:
        language_profile_id = _active_language_profile_id()
    if provider_id:
        command += ["--provider", provider_id]
    if language_profile_id:
        command += ["--language-profile", language_profile_id]
    if model:
        command += ["--model", model]
    if device:
        command += ["--device", device]
    if compute_type:
        command += ["--compute-type", compute_type]
    if asr_mode:
        command += ["--asr-mode", asr_mode]
    if language:
        command += ["--language", language]
    if local_files_only:
        command += ["--local-files-only"]
    if quality_preset:
        command += ["--quality-preset", quality_preset]
    if word_timestamps is True:
        command += ["--word-timestamps"]
    elif word_timestamps is False:
        command += ["--no-word-timestamps"]
    if resegment_subtitles is True:
        command += ["--resegment-subtitles"]
    elif resegment_subtitles is False:
        command += ["--no-resegment-subtitles"]
    if asr_retry_mode:
        command += ["--asr-retry-mode", str(asr_retry_mode)]
    if asr_hotword_prompt:
        command += ["--asr-hotword-prompt", asr_hotword_prompt]
    if translation_reliability_mode is not None:
        command += ["--translation-reliability-mode", translation_reliability_mode]
    if translation_max_extra_requests is not None:
        command += ["--translation-max-extra-requests", str(translation_max_extra_requests)]
    if translation_strategy_mode is not None:
        command += ["--translation-strategy-mode", translation_strategy_mode]
    if translation_scene_gap_seconds is not None:
        command += [
            "--translation-scene-gap-seconds", str(translation_scene_gap_seconds)
        ]
    if not translate_enabled:
        command += ["--no-translate"]
    command += ["--subtitle-formats", ",".join(subtitle_formats)]
    command += ["--ass-style-id", ass_style_id]
    return command


def _pipeline_env() -> dict[str, str]:
    return build_child_process_env(PROJECT_ROOT, PATHS)


def _append_pipeline_log_header(
    *,
    action_label: str,
    command: list[str],
    provider_id: str,
    language_profile_id: str,
    hf_endpoint: str,
    local_files_only: bool,
    subtitle_formats: list[str],
) -> None:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with PIPELINE_LOG.open("a", encoding="utf-8") as log:
        log.write(f"\n{'=' * 60}\n")
        log.write(f"  [{action_label}] 开始于 {started_at}\n")
        log.write(f"  Action: {action_label}\n")
        if provider_id:
            log.write(f"  Provider: {provider_id}\n")
        if language_profile_id:
            log.write(f"  Language Profile: {language_profile_id}\n")
        if hf_endpoint:
            log.write(f"  HF_ENDPOINT: {hf_endpoint}\n")
        if local_files_only:
            log.write("  Local files only: true\n")
        log.write(f"  Subtitle formats: {','.join(subtitle_formats)}\n")
        if "ass" in subtitle_formats:
            log.write(f"  ASS: {ASS_RESERVED_MESSAGE}\n")
        log.write(f"{'=' * 60}\n")


def _active_provider_id() -> str:
    try:
        from provider_store import get_active_provider

        active = get_active_provider()
        return str(active.get("id", "")) if active else ""
    except Exception:
        return ""


def _active_language_profile_id() -> str:
    try:
        from language_profile_store import get_active_language_profile

        active = get_active_language_profile()
        return str(active.get("id", "")) if active else ""
    except Exception:
        return ""


def _clean_log_line(line: str) -> str:
    had_newline = str(line).endswith(("\n", "\r"))
    cleaned = sanitize_event_text(redact_project_path(line, PROJECT_ROOT)).rstrip("\r\n")
    return cleaned + ("\n" if had_newline else "")
