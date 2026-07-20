from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SRC_SUBDIRS = ("core", "pipeline", "config", "web", "tools")
RELEASE_MARKER = ".portable-layout"


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved roots for source, release, and packaged installer layouts.

    project_root is the writable runtime root that owns input/output/work/logs,
    models, .cache, and tools. app_root is the code root that owns src, web,
    config, and scripts.

    - In a source checkout: project_root == app_root.
    - In a release layout: project_root is the release root; app_root is
      release_root/app.
    - In a packaged layout: project_root is the user-writable data root;
      app_root is the bundled read-only code root under the install directory.
    """

    layout: str
    project_root: Path
    app_root: Path
    src_root: Path
    runtime_root: Path

    @property
    def tools_dir(self) -> Path:
        if self.layout == "packaged":
            return self._packaged_root / "tools"
        return self.project_root / "tools"

    @property
    def python_runtime_dir(self) -> Path:
        if self.layout == "packaged":
            return self._packaged_root / "python"
        if self.layout == "release":
            return self.runtime_root / "python"
        return self.tools_dir / "python"

    @property
    def config_root(self) -> Path:
        if self.layout == "packaged":
            return (self.project_root / "config").resolve()
        return self.project_root / "config"

    @property
    def models_dir(self) -> Path:
        return self.project_root / "models"

    @property
    def output_dir(self) -> Path:
        return self.project_root / "output"

    @property
    def work_dir(self) -> Path:
        return self.project_root / "work"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.project_root / ".cache"

    @property
    def tmp_dir(self) -> Path:
        return self.project_root / ".tmp"

    @property
    def uploads_dir(self) -> Path:
        return self.project_root / "uploads"

    @property
    def _packaged_root(self) -> Path:
        """Return the packaged install root (bundled read-only resources)."""
        if self.layout == "packaged":
            env = os.environ.get("CINESUB_PACKAGED_ROOT")
            if env:
                return Path(env).resolve()
        return self.project_root

    @property
    def pythonpath_entries(self) -> tuple[Path, ...]:
        return (self.app_root, *(self.src_root / sub for sub in SRC_SUBDIRS))

    def pythonpath(self) -> str:
        return os.pathsep.join(str(path) for path in self.pythonpath_entries)


def resolve_runtime_paths(anchor: Path | str | None = None) -> RuntimePaths:
    """Resolve runtime paths without relying on the current working directory.

    The default anchor is this module file. Tests and controlled callers may
    pass an explicit file or directory anchor to simulate source or release
    layouts. This function is intentionally read-only.

    Packaged layout (v0.5+ Windows installer) is triggered by the
    CINESUB_PACKAGED_ROOT environment variable set by the Electron shell.
    """

    # Packaged layout takes priority (set by Electron installer shell)
    packaged_root_env = os.environ.get("CINESUB_PACKAGED_ROOT")
    if packaged_root_env:
        packaged_root = Path(packaged_root_env).resolve()
        app_root = packaged_root / "backend"
        src_root = app_root / "src"
        local_appdata = Path(
            os.environ.get("LOCALAPPDATA")
            or Path.home() / "AppData" / "Local"
        )
        user_data = Path(
            os.environ.get("CINESUB_USER_DATA_ROOT")
            or (local_appdata / "CineSubStudio")
        ).expanduser().resolve()
        runtime_root = user_data / "runtime"
        return RuntimePaths(
            layout="packaged",
            project_root=user_data,
            app_root=app_root,
            src_root=src_root,
            runtime_root=runtime_root,
        )

    anchor_path = Path(anchor).resolve() if anchor is not None else Path(__file__).resolve()
    src_root = _find_src_root(anchor_path)
    app_root = src_root.parent

    release_root = app_root.parent if app_root.name.lower() == "app" else app_root
    runtime_root = release_root / "runtime"
    if _is_release_layout(release_root, app_root, src_root, runtime_root):
        return RuntimePaths(
            layout="release",
            project_root=release_root,
            app_root=app_root,
            src_root=src_root,
            runtime_root=runtime_root,
        )

    project_root = app_root
    return RuntimePaths(
        layout="source",
        project_root=project_root,
        app_root=app_root,
        src_root=src_root,
        runtime_root=project_root / "runtime",
    )


def _find_src_root(anchor: Path) -> Path:
    start = anchor if anchor.is_dir() else anchor.parent
    candidates = (start, *start.parents)
    for candidate in candidates:
        if _looks_like_src_root(candidate):
            return candidate.resolve()
        nested_src = candidate / "src"
        if _looks_like_src_root(nested_src):
            return nested_src.resolve()
    raise RuntimeError(f"Could not locate CineSub src root from anchor: {anchor}")


def _looks_like_src_root(path: Path) -> bool:
    return (
        path.name == "src"
        and (path / "tools").is_dir()
        and (path / "web").is_dir()
        and (path / "core").is_dir()
    )


def _is_release_layout(release_root: Path, app_root: Path, src_root: Path, runtime_root: Path) -> bool:
    marker_ok = (release_root / RELEASE_MARKER).is_file() or (runtime_root / RELEASE_MARKER).is_file()
    return (
        marker_ok
        and app_root.name.lower() == "app"
        and (release_root / "app").resolve() == app_root.resolve()
        and (app_root / "src").resolve() == src_root.resolve()
        and runtime_root.is_dir()
    )
