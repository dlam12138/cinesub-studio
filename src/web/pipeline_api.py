from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from runtime_env import add_project_cuda_to_env
from subtitle_model import ASS_RESERVED_MESSAGE, DEFAULT_ASS_STYLE_ID, normalize_subtitle_formats


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = PROJECT_ROOT / "work"
PIPELINE_STATES_DIR = WORK_DIR / "states"
PIPELINE_LOG = PROJECT_ROOT / "logs" / "pipeline.log"

PIPELINE_TASK: dict[str, Any] = {
    "running": False,
    "pid": None,
    "action": "",
    "started_at": 0,
    "finished_at": 0,
    "returncode": None,
    "error": "",
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
        return dict(PIPELINE_TASK)


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
            "file": raw.get("file", ""),
            "input_path": raw.get("input_path", ""),
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "status": effective_status,
            "raw_status": status,
            "status_label": STATUS_LABELS.get(effective_status, effective_status),
            "progress": percent,
            "retry_count": raw.get("retry_count", 0),
            "max_retries": raw.get("max_retries", 0),
            "error": raw.get("error", ""),
            "error_stage": raw.get("error_stage", ""),
            "updated_at": raw.get("updated_at", 0),
            "warning": warning,
            "recoverable": recovery["recoverable"],
            "recovery_action": recovery["recovery_action"],
            "recovery_label": recovery["recovery_label"],
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
        "task": task_info,
        "stale_running": stale_running,
        "stale_running_count": stale_running_count,
        "recoverable_failed_count": recoverable_failed_count,
        "can_retry_failed": recoverable_failed_count > 0,
    }


def run_pipeline_command(action: str, timeout: int = 30, input_dir: str = "") -> dict:
    command = [
        sys.executable,
        "-B",
        str(PROJECT_ROOT / "src" / "pipeline" / "batch_worker.py"),
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
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode,
        }
        if action == "status":
            payload["progress"] = pipeline_progress()
        return payload
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": action, "error": f"命令超时（{timeout}s）"}
    except FileNotFoundError:
        return {"ok": False, "command": action, "error": f"Python 解释器未找到: {sys.executable}"}
    except Exception as exc:
        return {"ok": False, "command": action, "error": str(exc)}


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
    language: str = "",
    hf_endpoint: str = "",
    local_files_only: bool = False,
    subtitle_formats: list[str] | str | None = None,
    ass_style_id: str = "",
) -> tuple[dict, int]:
    with PIPELINE_TASK_LOCK:
        if PIPELINE_TASK["running"]:
            return {"ok": False, "error": "已有流水线任务正在运行，请等待完成。"}, 409
        PIPELINE_TASK.update({
            "running": True,
            "pid": None,
            "action": action,
            "started_at": time.time(),
            "finished_at": 0,
            "returncode": None,
            "error": "",
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
            "language": language,
            "hf_endpoint": hf_endpoint,
            "local_files_only": local_files_only,
            "subtitle_formats": subtitle_formats,
            "ass_style_id": ass_style_id,
        },
        daemon=True,
    )
    thread.start()
    label = "input 目录处理" if action == "run" else "retry-failed"
    return {"ok": True, "message": f"{label} 已启动。"}, 202


def run_pipeline_background(
    action: str,
    provider_id: str = "",
    language_profile_id: str = "",
    input_dir: str = "",
    model: str = "small",
    device: str = "auto",
    compute_type: str = "",
    translate_enabled: bool = True,
    language: str = "",
    hf_endpoint: str = "",
    local_files_only: bool = False,
    subtitle_formats: list[str] | str | None = None,
    ass_style_id: str = "",
) -> None:
    subtitle_formats_list = normalize_subtitle_formats(subtitle_formats)
    ass_style_id = ass_style_id or DEFAULT_ASS_STYLE_ID
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    command = _build_background_command(
        action=action,
        provider_id=provider_id,
        language_profile_id=language_profile_id,
        input_dir=input_dir,
        model=model,
        device=device,
        compute_type=compute_type,
        translate_enabled=translate_enabled,
        language=language,
        local_files_only=local_files_only,
        subtitle_formats=subtitle_formats_list,
        ass_style_id=ass_style_id,
    )
    env = _pipeline_env()
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
            log.write(f"\n[{action_label}] 异常: {exc}\n")
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["error"] = str(exc)
    finally:
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["running"] = False
            PIPELINE_TASK["pid"] = None
            PIPELINE_TASK["finished_at"] = time.time()
            PIPELINE_TASK["returncode"] = returncode


def read_pipeline_log() -> dict:
    if not PIPELINE_LOG.exists():
        return {"ok": True, "lines": [], "text": ""}
    try:
        text = PIPELINE_LOG.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc), "lines": [], "text": ""}
    lines = text.splitlines()[-200:]
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


def _recovery_state(raw: dict, effective_status: str) -> dict:
    if effective_status == "failed":
        return _recovery("retry_failed", True)
    if effective_status == "stale":
        return _recovery("stale_running_warning", False)
    if effective_status == "completed":
        if _completed_state_outputs_valid(raw):
            return _recovery("skip_completed", False)
        return _recovery("not_recoverable", False)
    if effective_status == "pending" and _has_reusable_outputs(raw):
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
    return {
        "recovery_action": action,
        "recoverable": recoverable,
        "recovery_label": labels.get(action, action),
    }


def _completed_state_outputs_valid(raw: dict) -> bool:
    paths = _state_output_paths(raw)
    if not paths:
        return False
    return all(_is_valid_file(path) for path in paths)


def _has_reusable_outputs(raw: dict) -> bool:
    paths = [
        raw.get("audio_path", ""),
        raw.get("source_srt", ""),
        raw.get("translated_srt", ""),
        raw.get("bilingual_srt", ""),
        raw.get("quality_report", ""),
    ]
    return any(_is_valid_file(path) for path in paths)


def _state_output_paths(raw: dict) -> list[str]:
    paths = [raw.get("source_srt", "")]
    paths.extend(path for path in [raw.get("translated_srt", ""), raw.get("bilingual_srt", "")] if path)
    if raw.get("quality_report"):
        paths.append(raw.get("quality_report", ""))
    return [path for path in paths if path]


def _is_valid_file(path: str) -> bool:
    if not path:
        return False
    try:
        file_path = Path(path)
        return file_path.is_file() and file_path.stat().st_size > 0
    except OSError:
        return False


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
    language: str,
    local_files_only: bool,
    subtitle_formats: list[str],
    ass_style_id: str,
) -> list[str]:
    if action == "run":
        command = [
            sys.executable,
            "-B",
            str(PROJECT_ROOT / "src" / "pipeline" / "batch_worker.py"),
            "--input",
            input_dir if input_dir else str(PROJECT_ROOT / "input"),
        ]
    else:
        command = [
            sys.executable,
            "-B",
            str(PROJECT_ROOT / "src" / "pipeline" / "batch_worker.py"),
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
    if language:
        command += ["--language", language]
    if local_files_only:
        command += ["--local-files-only"]
    if not translate_enabled:
        command += ["--no-translate"]
    command += ["--subtitle-formats", ",".join(subtitle_formats)]
    command += ["--ass-style-id", ass_style_id]
    return command


def _pipeline_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    src = PROJECT_ROOT / "src"
    env["PYTHONPATH"] = ";".join(str(src / sub) for sub in ["core", "pipeline", "config", "web", "tools"])
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    _clear_proxy_env(env)
    add_project_cuda_to_env(env)
    return env


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
        log.write(f"  命令: {' '.join(command)}\n")
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


def _clear_proxy_env(env: dict[str, str]) -> None:
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GIT_HTTP_PROXY",
        "GIT_HTTPS_PROXY",
    ):
        env.pop(key, None)


def _clean_log_line(line: str) -> str:
    project = str(PROJECT_ROOT)
    project_alt = project.replace("\\", "/")
    return line.replace(project, ".").replace(project_alt, ".")
