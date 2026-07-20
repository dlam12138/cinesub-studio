from __future__ import annotations

import os
from pathlib import Path

from runtime_env import add_project_cuda_to_env
from runtime_paths import RuntimePaths, resolve_runtime_paths


SRC_SUBDIRS = ("core", "pipeline", "config", "web", "tools")
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "GIT_HTTP_PROXY",
    "GIT_HTTPS_PROXY",
)


def build_child_process_env(project_root: Path, paths: RuntimePaths | None = None) -> dict[str, str]:
    env = os.environ.copy()
    root = Path(project_root)
    env["HF_HOME"] = str(root / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(root / ".cache" / "huggingface" / "hub")
    env["PYTHONPATH"] = _pythonpath_for(root, paths)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    _clear_proxy_env(env)
    add_project_cuda_to_env(env)
    return env


def redact_project_path(text: str, project_root: Path) -> str:
    project = str(Path(project_root))
    project_alt = project.replace("\\", "/")
    return text.replace(project, ".").replace(project_alt, ".")


def _clear_proxy_env(env: dict[str, str]) -> None:
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)


def _pythonpath_for(project_root: Path, paths: RuntimePaths | None) -> str:
    if paths is not None:
        return paths.pythonpath()
    resolved = resolve_runtime_paths()
    if project_root.resolve() == resolved.project_root:
        return resolved.pythonpath()
    src = project_root / "src"
    entries = (project_root, *(src / sub for sub in SRC_SUBDIRS))
    return os.pathsep.join(str(path) for path in entries)
