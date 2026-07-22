from __future__ import annotations

import shutil
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from asr_model_locator import (
    MODEL_CATALOG,
    MODEL_SOURCES,
    installed_models,
    locate_asr_model,
    model_plan,
    model_target_dir,
    validate_model_directory,
)
from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths()
MODEL_DIR = PATHS.models_dir
HF_CACHE_DIR = PATHS.cache_dir / "huggingface" / "hub"
TMP_DIR = PATHS.tmp_dir

DOWNLOAD_LOCK = threading.Lock()
DOWNLOAD_TASK: dict[str, Any] = {
    "id": "",
    "status": "idle",
    "model": "",
    "source": "",
    "stage": "",
    "progress": None,
    "started_at": 0,
    "finished_at": 0,
    "error": "",
}


class AsrModelDownloadConflict(RuntimeError):
    pass


def get_asr_models_payload(selected: str = "small", source: str = "official") -> dict:
    plan = model_plan(
        selected,
        source,
        model_dir=MODEL_DIR,
        hf_cache_dir=HF_CACHE_DIR,
    )
    return {
        "ok": True,
        "installed": installed_models(MODEL_DIR, HF_CACHE_DIR),
        "selected": plan,
        "sources": [
            {"id": key, "label": value["label"], "url": value["endpoint"]}
            for key, value in MODEL_SOURCES.items()
        ],
    }


def missing_model_payload(model_name: str, source: str = "official") -> dict | None:
    candidate = Path(model_name).expanduser()
    if candidate.is_absolute():
        location = locate_asr_model(model_name, MODEL_DIR, HF_CACHE_DIR)
        if location.available:
            return None
        return {
            "ok": False,
            "code": "asr_model_required",
            "error": "指定的本地 ASR 模型目录不存在或不完整。",
            "model": model_name,
            "confirmation_required": True,
            "message": "所选 ASR 模型需要先安装到本地。",
            "model_plan": {
                "model": model_name,
                "available": False,
                "download_required": False,
                "download_supported": False,
                "repo_id": "本地目录",
                "estimated_size": "未知",
                "source": "local",
                "source_label": "用户提供的本地目录",
                "target_dir": str(candidate),
                "missing_files": list(location.missing_files),
            },
        }
    if model_name not in MODEL_CATALOG:
        raise ValueError("Web tasks only support the listed ASR models.")
    plan = model_plan(
        model_name,
        source,
        model_dir=MODEL_DIR,
        hf_cache_dir=HF_CACHE_DIR,
    )
    if plan["available"]:
        return None
    return {
        "ok": False,
        "code": "asr_model_required",
        "error": f"所选 ASR 模型 {model_name} 尚未安装。",
        "model": model_name,
        "confirmation_required": True,
        "message": (
            "质量优先模式需要本地 large-v3 模型。"
            if model_name == "large-v3"
            else f"所选 ASR 模型 {model_name} 需要先安装到本地。"
        ),
        "model_plan": plan,
    }


def resolve_web_model(model_name: str) -> str:
    location = locate_asr_model(model_name, MODEL_DIR, HF_CACHE_DIR)
    if not location.available:
        raise ValueError(f"所选 ASR 模型 {model_name} 尚未安装。")
    return location.local_path


def get_download_task() -> dict:
    with DOWNLOAD_LOCK:
        return {"ok": True, "task": dict(DOWNLOAD_TASK)}


def download_is_running() -> bool:
    with DOWNLOAD_LOCK:
        return DOWNLOAD_TASK["status"] == "downloading"


def start_model_download(
    *,
    model_name: str,
    source: str,
    confirmed: bool,
    busy_reason: str = "",
) -> tuple[dict, int]:
    if model_name not in MODEL_CATALOG:
        raise ValueError("Unsupported ASR model.")
    if source not in MODEL_SOURCES:
        raise ValueError("Unsupported ASR model source.")
    if confirmed is not True:
        raise ValueError("Model download requires explicit confirmation.")
    if busy_reason:
        raise AsrModelDownloadConflict(busy_reason)

    current = model_plan(
        model_name,
        source,
        model_dir=MODEL_DIR,
        hf_cache_dir=HF_CACHE_DIR,
    )
    if current["available"]:
        return {"ok": True, "already_available": True, "model": current}, 200

    with DOWNLOAD_LOCK:
        if DOWNLOAD_TASK["status"] == "downloading":
            raise AsrModelDownloadConflict("已有模型下载正在进行，请等待完成。")
        task_id = uuid.uuid4().hex[:12]
        DOWNLOAD_TASK.update(
            {
                "id": task_id,
                "status": "downloading",
                "model": model_name,
                "source": source,
                "stage": "preparing",
                "progress": None,
                "started_at": time.time(),
                "finished_at": 0,
                "error": "",
            }
        )

    threading.Thread(
        target=_run_download,
        args=(task_id, model_name, source),
        daemon=True,
    ).start()
    return {"ok": True, "task": dict(DOWNLOAD_TASK), "model_plan": current}, 202


def _run_download(task_id: str, model_name: str, source: str) -> None:
    staging = TMP_DIR / "asr-download" / task_id
    target = model_target_dir(model_name, MODEL_DIR)
    try:
        _set_task(task_id, stage="downloading")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=False)
        target.parent.mkdir(parents=True, exist_ok=True)

        from huggingface_hub import snapshot_download

        endpoint = str(MODEL_SOURCES[source]["endpoint"])
        snapshot_download(
            repo_id=str(MODEL_CATALOG[model_name]["repo_id"]),
            local_dir=str(staging),
            local_dir_use_symlinks=False,
            endpoint=endpoint,
        )

        _set_task(task_id, stage="validating")
        valid, missing = validate_model_directory(staging)
        if not valid:
            raise RuntimeError("下载完成但模型不完整，缺少：" + ", ".join(missing))

        _set_task(task_id, stage="installing")
        if target.exists():
            valid_target, _ = validate_model_directory(target)
            if valid_target:
                shutil.rmtree(staging)
            else:
                shutil.rmtree(target)
                staging.replace(target)
        else:
            staging.replace(target)
        _set_task(
            task_id,
            status="completed",
            stage="completed",
            progress=100,
            finished_at=time.time(),
        )
    except Exception as exc:
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError:
            pass
        _set_task(
            task_id,
            status="failed",
            stage="failed",
            progress=None,
            finished_at=time.time(),
            error=_clean_error(str(exc)),
        )


def _set_task(task_id: str, **updates: Any) -> None:
    with DOWNLOAD_LOCK:
        if DOWNLOAD_TASK["id"] != task_id:
            return
        DOWNLOAD_TASK.update(updates)


def _clean_error(message: str) -> str:
    text = (message or "模型下载失败。").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "sk-***", text, flags=re.IGNORECASE)
    text = re.sub(
        r"((?:api[_-]?key|token|secret)\s*[:=]\s*)\S+",
        r"\1***",
        text,
        flags=re.IGNORECASE,
    )
    return text[:500]
