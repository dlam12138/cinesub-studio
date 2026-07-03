from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_NAME = "cinesub-portable"
RELEASE_MARKER = ".portable-layout"
RUNTIME_DIRS = (
    "config",
    "input",
    "output",
    "work",
    "logs",
    "uploads",
    "models",
    ".cache",
    ".tmp",
)
APP_DIRS = ("src", "web", "scripts")
APP_FILES = (
    "README.md",
    "AGENTS.md",
    "requirements.txt",
    "start_app.py",
    "start_web.ps1",
    "install.ps1",
    "run_transcribe.ps1",
    "analyze_subtitles.ps1",
)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "dist",
    ".cache",
    ".tmp",
    "models",
    "uploads",
    "output",
    "work",
    "logs",
    "archive",
    "failed",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
EXCLUDED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".log",
    ".tmp",
    ".bak",
    ".srt",
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".wav",
    ".mp3",
    ".flac",
    ".aac",
    ".zip",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".quality_report.json",
    ".state.json",
    ".lang.json",
}


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    output_dir: Path
    app_dir: Path
    python_runtime_dir: Path
    ffmpeg_copied: bool


def build_portable_release(
    *,
    repo_root: Path | str | None = None,
    output: Path | str | None = None,
    python_runtime: Path | str | None = None,
    force: bool = False,
) -> BuildResult:
    root = Path(repo_root).resolve() if repo_root is not None else REPO_ROOT
    output_dir = _resolve_output_path(output, root)
    _ensure_safe_output_path(output_dir, root)

    python_source = _resolve_input_path(python_runtime, root / "tools" / "python", root)
    python_exe = python_source / "python.exe"
    if not python_exe.is_file():
        raise BuildError(
            f"Portable Python runtime is missing: {python_exe}. "
            "Provide tools/python/python.exe or pass --python-runtime."
        )

    if output_dir.exists():
        if not force:
            raise BuildError(f"Output directory already exists. Use --force to replace it: {output_dir}")
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True)
    app_dir = output_dir / "app"
    runtime_python_dir = output_dir / "runtime" / "python"

    _copy_app_whitelist(root, app_dir)
    shutil.copytree(python_source, runtime_python_dir)

    ffmpeg_source = root / "tools" / "ffmpeg"
    ffmpeg_copied = False
    if ffmpeg_source.exists():
        shutil.copytree(ffmpeg_source, output_dir / "tools" / "ffmpeg")
        ffmpeg_copied = True

    for name in RUNTIME_DIRS:
        (output_dir / name).mkdir(parents=True, exist_ok=True)
    (output_dir / RELEASE_MARKER).write_text("portable layout\n", encoding="utf-8")
    _write_start_bat(output_dir / "start_app.bat")

    return BuildResult(
        output_dir=output_dir,
        app_dir=app_dir,
        python_runtime_dir=runtime_python_dir,
        ffmpeg_copied=ffmpeg_copied,
    )


def _resolve_input_path(value: Path | str | None, default: Path, repo_root: Path) -> Path:
    path = Path(value) if value is not None else default
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _resolve_output_path(value: Path | str | None, repo_root: Path) -> Path:
    path = Path(value) if value is not None else repo_root / "dist" / DEFAULT_OUTPUT_NAME
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _ensure_safe_output_path(output_dir: Path, repo_root: Path) -> None:
    dist_root = (repo_root / "dist").resolve()
    forbidden = {repo_root, repo_root.parent, dist_root}
    anchor = output_dir.anchor
    if anchor:
        forbidden.add(Path(anchor).resolve())
    if output_dir in forbidden:
        raise BuildError(f"Refusing unsafe output directory: {output_dir}")
    try:
        output_dir.relative_to(dist_root)
    except ValueError as exc:
        raise BuildError(f"Output directory must be under repo dist/: {output_dir}") from exc
    if output_dir.parent != dist_root:
        raise BuildError(f"Output directory must be a direct child of repo dist/: {output_dir}")


def _copy_app_whitelist(repo_root: Path, app_dir: Path) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    for dirname in APP_DIRS:
        source = repo_root / dirname
        if source.exists():
            shutil.copytree(source, app_dir / dirname, ignore=_ignore_app_artifacts)
    for filename in APP_FILES:
        source = repo_root / filename
        if source.is_file():
            shutil.copy2(source, app_dir / filename)


def _ignore_app_artifacts(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if path.is_dir() and name in EXCLUDED_DIR_NAMES:
            ignored.add(name)
            continue
        if path.is_file() and _is_excluded_file(path):
            ignored.add(name)
    return ignored


def _is_excluded_file(path: Path) -> bool:
    name = path.name
    if any(name.endswith(suffix) for suffix in EXCLUDED_FILE_SUFFIXES):
        return True
    if name.endswith(".review_needed.srt"):
        return True
    return False


def _write_start_bat(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal",
                'cd /d "%~dp0"',
                "",
                'if not exist "runtime\\python\\python.exe" (',
                "  echo [ERROR] Portable Python runtime is missing: runtime\\python\\python.exe",
                "  echo Please provide runtime\\python\\ or rebuild the portable release with --python-runtime.",
                "  pause",
                "  exit /b 1",
                ")",
                "",
                '"runtime\\python\\python.exe" -B "app\\start_app.py"',
                "exit /b %ERRORLEVEL%",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the CineSub portable release prototype.")
    parser.add_argument("--output", default=str(Path("dist") / DEFAULT_OUTPUT_NAME))
    parser.add_argument("--python-runtime", default=str(Path("tools") / "python"))
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = build_portable_release(
            output=args.output,
            python_runtime=args.python_runtime,
            force=args.force,
        )
    except BuildError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"Portable release prototype: {result.output_dir}")
    print(f"App files: {result.app_dir}")
    print(f"Python runtime: {result.python_runtime_dir}")
    print(f"FFmpeg copied: {'yes' if result.ffmpeg_copied else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
