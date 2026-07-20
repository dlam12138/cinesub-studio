from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_paths import RuntimePaths, resolve_runtime_paths


_LOCK = threading.Lock()
_SECRET_KEYS = re.compile(
    r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|secret|password|bearer)"
)
_WINDOWS_PATH = re.compile(r"(?i)[a-z]:[\\/][^\r\n\"']+")
_INLINE_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|secret|password|bearer)"
    r"(\s*[:=]\s*)\S+"
)


class DiagnosticBundleBusy(RuntimeError):
    pass


def resolve_diagnostic_bundle(file_name: str, paths: RuntimePaths | None = None) -> Path | None:
    paths = paths or resolve_runtime_paths()
    name = str(file_name or "").strip()
    if not name or Path(name).name != name or not name.startswith("cinesub-diagnostics-") or not name.endswith(".zip"):
        return None
    root = (paths.project_root / "reports" / "diagnostics").resolve()
    candidate = (root / name).resolve()
    try:
        if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return candidate


def _sanitize(value: Any, paths: RuntimePaths) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SECRET_KEYS.search(str(key)) and not str(key).lower().endswith(("_present", "_masked"))
            else _sanitize(item, paths)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item, paths) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item, paths) for item in value]
    if isinstance(value, str):
        text = value.replace(str(paths.project_root), "[project-root]")
        text = text.replace(str(paths.app_root), "[app-root]")
        text = _INLINE_SECRET.sub(r"\1\2[redacted]", text)
        return _WINDOWS_PATH.sub("[local-path]", text)[:4000]
    return value


def _json_bytes(value: Any, paths: RuntimePaths) -> bytes:
    return (json.dumps(_sanitize(value, paths), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _safe_task_states(paths: RuntimePaths) -> dict:
    states = []
    state_root = paths.work_dir / "states"
    for path in sorted(state_root.glob("*.state.json")) if state_root.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        states.append({
            "file": Path(str(data.get("file") or path.stem)).name,
            "status": data.get("status"),
            "stage": data.get("stage"),
            "retry_count": data.get("retry_count"),
            "max_retries": data.get("max_retries"),
            "error_stage": data.get("error_stage"),
            "error": str(data.get("error") or "")[:500],
        })
    return {"count": len(states), "states": states}


def _tail(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def _validate_entries(entries: dict[str, bytes]) -> None:
    forbidden = re.compile(
        rb"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password)"
        rb"\s*[\"']?\s*[:=]\s*[\"']?\s*(?!\[redacted\])[^\s\"']{6,}"
    )
    for name, data in entries.items():
        if forbidden.search(data) or re.search(rb"(?i)[a-z]:\\Users\\", data):
            raise RuntimeError(f"diagnostic bundle safety scan failed for {name}")


def create_diagnostic_bundle(paths: RuntimePaths | None = None) -> dict:
    paths = paths or resolve_runtime_paths()
    if not _LOCK.acquire(blocking=False):
        raise DiagnosticBundleBusy("A diagnostic bundle is already being generated.")
    try:
        from app_info import get_app_info
        from provider_store import list_providers
        from runtime_api import get_runtime_diagnostics

        generated = datetime.now(timezone.utc)
        metadata = {
            "schema_version": 1,
            "generated_at": generated.isoformat(),
            "privacy": {
                "media_included": False,
                "subtitle_text_included": False,
                "translation_cache_included": False,
                "secrets_included": False,
                "absolute_paths_included": False,
            },
        }
        providers = []
        for provider in list_providers(mask_secret=True):
            providers.append({
                "id": provider.get("id"),
                "name": provider.get("name"),
                "protocol": provider.get("protocol"),
                "translation_model": provider.get("translation_model"),
                "enabled": provider.get("enabled", True),
                "api_key_present": bool(provider.get("api_key_masked")),
            })
        entries = {
            "metadata.json": _json_bytes(metadata, paths),
            "app_info.json": _json_bytes(get_app_info(paths), paths),
            "runtime_diagnostics.json": _json_bytes(get_runtime_diagnostics(), paths),
            "provider_metadata.json": _json_bytes({"providers": providers}, paths),
            "task_states.json": _json_bytes(_safe_task_states(paths), paths),
            "logs/pipeline.log.txt": _json_bytes(
                {"tail": _tail(paths.logs_dir / "pipeline.log")}, paths
            ),
            "logs/pipeline.events.jsonl.txt": _json_bytes(
                {"tail": _tail(paths.logs_dir / "pipeline.events.jsonl")}, paths
            ),
        }
        _validate_entries(entries)
        output_dir = paths.project_root / "reports" / "diagnostics"
        output_dir.mkdir(parents=True, exist_ok=True)
        name = f"cinesub-diagnostics-{generated.strftime('%Y%m%dT%H%M%SZ')}.zip"
        target = output_dir / name
        temporary = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry_name, content in entries.items():
                archive.writestr(entry_name, content)
        with zipfile.ZipFile(temporary, "r") as archive:
            scanned = {name: archive.read(name) for name in archive.namelist()}
        _validate_entries(scanned)
        temporary.replace(target)
        return {
            "ok": True,
            "file": target.name,
            "bytes": target.stat().st_size,
            "download_id": target.name,
            "restricted": True,
        }
    finally:
        _LOCK.release()
