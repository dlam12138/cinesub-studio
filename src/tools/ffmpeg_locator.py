from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FFMPEG_SOURCE_LABELS = {
    "env": "环境变量",
    "bundled": "项目内置",
    "path": "系统 PATH",
    "not_found": "未找到",
}


def find_ffmpeg(project_root: Path | str | None = None) -> str | None:
    """Return the bundled ffmpeg path, falling back to PATH.

    The project should work on machines without a system ffmpeg install, so
    project-local locations are checked first.
    """
    info = find_ffmpeg_info(project_root)
    return info["path"] if info["ok"] else None


def find_ffmpeg_info(project_root: Path | str | None = None) -> dict:
    """Return the ffmpeg path plus its source without changing lookup order."""
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    for env_name in ("CINESUB_FFMPEG", "FFMPEG_PATH"):
        found = _as_ffmpeg_path(os.environ.get(env_name), exe_name)
        if found:
            return _info(True, str(found), "env")

    for candidate in _direct_candidates(root, exe_name):
        if _is_ffmpeg_file(candidate, exe_name):
            return _info(True, str(candidate), "bundled")

    for base in (root / "tools", root / "bin", root / "vendor", root / "ffmpeg"):
        if not base.exists():
            continue
        for candidate in base.rglob(exe_name):
            if _is_ffmpeg_file(candidate, exe_name):
                return _info(True, str(candidate), "bundled")

    system = shutil.which("ffmpeg")
    if system and _is_ffmpeg_file(Path(system), exe_name):
        return _info(True, system, "path")
    return _info(False, "", "not_found")


def _info(ok: bool, path: str, source: str) -> dict:
    return {
        "ok": ok,
        "path": path,
        "source": source,
        "source_label": FFMPEG_SOURCE_LABELS[source],
    }


def _direct_candidates(root: Path, exe_name: str) -> list[Path]:
    return [
        root / "tools" / "ffmpeg" / "bin" / exe_name,
        root / "tools" / exe_name,
        root / "tools" / "ffmpeg" / exe_name,
        root / "bin" / exe_name,
        root / "bin" / "ffmpeg" / exe_name,
        root / "bin" / "ffmpeg" / "bin" / exe_name,
        root / "vendor" / "ffmpeg" / exe_name,
        root / "vendor" / "ffmpeg" / "bin" / exe_name,
        root / "ffmpeg" / exe_name,
        root / "ffmpeg" / "bin" / exe_name,
        root / "src" / "tools" / exe_name,
    ]


def _as_ffmpeg_path(value: str | None, exe_name: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_dir():
        path = path / exe_name
    if _is_ffmpeg_file(path, exe_name):
        return path
    return None


def _is_ffmpeg_file(path: Path, exe_name: str) -> bool:
    return path.is_file() and path.name.lower() == exe_name.lower()
