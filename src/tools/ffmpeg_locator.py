from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def find_ffmpeg(project_root: Path | str | None = None) -> str | None:
    """Return the bundled ffmpeg path, falling back to PATH.

    The project should work on machines without a system ffmpeg install, so
    project-local locations are checked first.
    """
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    for env_name in ("CINESUB_FFMPEG", "FFMPEG_PATH"):
        found = _as_ffmpeg_path(os.environ.get(env_name), exe_name)
        if found:
            return str(found)

    direct_candidates = [
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
    for candidate in direct_candidates:
        if candidate.is_file():
            return str(candidate)

    for base in (root / "tools", root / "bin", root / "vendor", root / "ffmpeg"):
        if not base.exists():
            continue
        for candidate in base.rglob(exe_name):
            if candidate.is_file():
                return str(candidate)

    system = shutil.which("ffmpeg")
    if system:
        return system
    return None


def _as_ffmpeg_path(value: str | None, exe_name: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_dir():
        path = path / exe_name
    if path.is_file():
        return path
    return None
