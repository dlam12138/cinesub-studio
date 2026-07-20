from __future__ import annotations

from pathlib import Path


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _sum_files(paths: list[Path]) -> tuple[int, int]:
    total = count = 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
                count += 1
        except OSError:
            continue
    return count, total


def storage_status(*, project_root: Path, work_dir: Path, upload_dir: Path) -> dict:
    audio_count, audio_size = _sum_files(list(work_dir.glob("*.16k.wav")))
    uploads = [path for path in upload_dir.iterdir()] if upload_dir.exists() else []
    upload_count, upload_size = _sum_files(uploads)
    translation_cache = work_dir / "translation-cache"
    cache_files = list(translation_cache.glob("*.json")) if translation_cache.exists() else []
    cache_count, cache_size = _sum_files(cache_files)
    return {
        "ok": True,
        "work_audio": {"path": str(work_dir), "count": audio_count, "bytes": audio_size, "display": format_bytes(audio_size)},
        "uploads": {"path": str(upload_dir), "count": upload_count, "bytes": upload_size, "display": format_bytes(upload_size)},
        "translation_cache": {
            "path": str(translation_cache), "count": cache_count, "bytes": cache_size,
            "display": format_bytes(cache_size), "managed_by_cleanup": False,
        },
        "model_cache": {"path": str(project_root / ".cache" / "huggingface"), "managed_by_cleanup": False},
        "note": "Stopping the web service does not automatically delete caches.",
    }


def cleanup_transient_files(*, project_root: Path, work_dir: Path, upload_dir: Path) -> dict:
    deleted: list[str] = []
    errors: list[str] = []
    targets = list(work_dir.glob("*.16k.wav")) if work_dir.exists() else []
    if upload_dir.exists():
        targets.extend(path for path in upload_dir.iterdir() if path.is_file())
    allowed = [work_dir.resolve(), upload_dir.resolve()]
    for target in targets:
        try:
            resolved = target.resolve()
            if not any(resolved.is_relative_to(root) for root in allowed):
                errors.append(f"Skipped unexpected path: {target.name}")
                continue
            size = resolved.stat().st_size
            resolved.unlink()
            deleted.append(f"{target.name} ({format_bytes(size)})")
        except OSError as exc:
            errors.append(f"{target.name}: {exc}")
    result = storage_status(project_root=project_root, work_dir=work_dir, upload_dir=upload_dir)
    result.update({"ok": not errors, "deleted": deleted, "errors": errors})
    return result
