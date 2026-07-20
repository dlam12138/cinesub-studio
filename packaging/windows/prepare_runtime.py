from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", "pip-cache", "test", "tests"}
REQUIRED_IMPORTS = ("faster_whisper", "ctranslate2", "av", "numpy", "requests")


def _ignore_runtime_noise(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in EXCLUDED_NAMES or name.endswith((".pyc", ".pyo"))
    }


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Required runtime directory is missing: {source}")
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=_ignore_runtime_noise,
    )


def _site_packages(venv_root: Path) -> Path:
    windows = venv_root / "Lib" / "site-packages"
    if windows.is_dir():
        return windows
    candidates = sorted((venv_root / "lib").glob("python*/site-packages"))
    if candidates:
        return candidates[-1]
    raise RuntimeError(f"Could not find site-packages under: {venv_root}")


def prepare_runtime(
    *,
    project_root: Path,
    destination: Path,
    require_cuda: bool = False,
) -> Path:
    portable_python = project_root / "tools" / "python"
    venv_root = project_root / ".venv"
    ffmpeg_root = project_root / "tools" / "ffmpeg"
    cuda_root = project_root / "tools" / "cuda"

    python_exe = portable_python / "python.exe"
    if not python_exe.is_file():
        raise RuntimeError(f"Portable Python 3.12 is missing: {python_exe}")
    if not (ffmpeg_root / "bin" / "ffmpeg.exe").is_file():
        raise RuntimeError(f"FFmpeg is missing: {ffmpeg_root / 'bin' / 'ffmpeg.exe'}")
    if not (ffmpeg_root / "bin" / "ffprobe.exe").is_file():
        raise RuntimeError(f"FFprobe is missing: {ffmpeg_root / 'bin' / 'ffprobe.exe'}")
    if require_cuda and not cuda_root.is_dir():
        raise RuntimeError(f"CUDA runtime is required but missing: {cuda_root}")

    resolved_destination = destination.resolve()
    if resolved_destination.exists():
        shutil.rmtree(resolved_destination)
    resolved_destination.mkdir(parents=True)

    staged_python = resolved_destination / "python"
    _copy_tree(portable_python, staged_python)
    _copy_tree(_site_packages(venv_root), staged_python / "Lib" / "site-packages")
    _copy_tree(ffmpeg_root, resolved_destination / "tools" / "ffmpeg")
    ffplay = resolved_destination / "tools" / "ffmpeg" / "bin" / "ffplay.exe"
    if ffplay.exists():
        ffplay.unlink()
    if require_cuda:
        _copy_tree(cuda_root, resolved_destination / "tools" / "cuda")

    # A copied venv is intentionally not part of the Electron portable runtime.
    pyvenv_cfg = staged_python / "pyvenv.cfg"
    if pyvenv_cfg.exists():
        pyvenv_cfg.unlink()

    validate_runtime(staged_python, require_cuda=require_cuda)
    return resolved_destination


def validate_runtime(python_root: Path, *, require_cuda: bool = False) -> None:
    python_exe = python_root / "python.exe"
    import_statement = ", ".join(REQUIRED_IMPORTS)
    code = (
        f"import {import_statement}; import pathlib, sys; "
        "root = pathlib.Path(sys.executable).resolve().parent; "
        "base = pathlib.Path(sys._base_executable).resolve().parent; "
        "assert root == base, (root, base); "
        "print(sys.version); print(sys.executable)"
    )
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PATH"] = (
        str(python_root)
        + os.pathsep
        + str(python_root / "DLLs")
        + os.pathsep
        + env.get("PATH", "")
    )
    completed = subprocess.run(
        [str(python_exe), "-I", "-B", "-c", code],
        cwd=str(python_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Staged Python runtime validation failed: {detail}")

    if require_cuda:
        cuda_root = python_root.parent / "tools" / "cuda"
        cublas = cuda_root / "cublas64_12.dll"
        cudnn = list(cuda_root.glob("cudnn*_9.dll"))
        if not cublas.is_file() or not cudnn:
            raise RuntimeError("Required CUDA runtime DLLs are incomplete.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage a relocatable Windows runtime.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args(argv)
    destination = prepare_runtime(
        project_root=args.project_root.resolve(),
        destination=args.destination,
        require_cuda=args.require_cuda,
    )
    print(f"Prepared and validated runtime: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
