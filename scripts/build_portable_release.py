from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
PRODUCT_SLUG = "CineSubStudio"
GITHUB_ASSET_LIMIT = 2 * 1024 * 1024 * 1024
MODEL_DIRECTORY = "models--Systran--faster-whisper-small"
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
REQUIRED_CUDA_FILES = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
WRITABLE_DATA_DIRS = (
    "config",
    "input",
    "output",
    "work",
    "logs",
    "uploads",
    "models",
    ".cache",
)
FORBIDDEN_PACKAGE_NAMES = {
    "start_app.bat",
    "start_web.ps1",
    "run_transcribe.ps1",
    "install.ps1",
    "analyze_subtitles.ps1",
    "ffplay.exe",
    "providers.local.json",
    "language_profiles.local.json",
}
FORBIDDEN_PACKAGE_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "tests",
    "research",
    "node_modules",
}
SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(
        r"""(?ix)
        ["']?(?:api_key|access_token|refresh_token|client_secret|password)["']?
        \s*[:=]\s*
        ["']([^"'\s]{12,})["']
        """
    ),
)


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    release_name: str
    zip_path: Path
    zip_sha256_path: Path
    cuda_addon_path: Path | None
    cuda_addon_sha256_path: Path | None
    split_cuda: bool
    zip_bytes: int


def _run(command: list[str], *, cwd: Path, timeout: int = 3600) -> None:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise BuildError(
            f"Command failed with exit code {completed.returncode}: "
            + subprocess.list2cmdline(command)
        )


def _release_name(version: str) -> str:
    return f"{PRODUCT_SLUG}-{version}-windows-x64-portable"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sha256_sidecar(path: Path) -> Path:
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{_sha256(path)}  {path.name}\n", encoding="utf-8")
    return sidecar


def _find_model_snapshot(model_root: Path) -> Path:
    direct = model_root / MODEL_DIRECTORY
    candidates = [direct]
    snapshots = direct / "snapshots"
    if snapshots.is_dir():
        candidates.extend(path for path in snapshots.iterdir() if path.is_dir())
    for candidate in candidates:
        if all((candidate / filename).is_file() for filename in REQUIRED_MODEL_FILES):
            return candidate
    raise BuildError(
        f"Complete faster-whisper small model was not found under: {direct}"
    )


def _validate_inputs(root: Path) -> Path:
    python_exe = root / "tools" / "python" / "python.exe"
    ffmpeg = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    ffprobe = root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    for path, label in (
        (python_exe, "portable Python"),
        (ffmpeg, "FFmpeg"),
        (ffprobe, "FFprobe"),
    ):
        if not path.is_file():
            raise BuildError(f"{label} is missing: {path}")
    cuda_root = root / "tools" / "cuda"
    for filename in REQUIRED_CUDA_FILES:
        path = cuda_root / filename
        if not path.is_file():
            raise BuildError(f"CUDA runtime is incomplete: {path}")
    return _find_model_snapshot(root / "models")


def _validate_portable_python(root: Path) -> None:
    python_exe = root / "tools" / "python" / "python.exe"
    code = (
        "import faster_whisper, ctranslate2, av, numpy, requests; "
        "print('portable runtime imports ok')"
    )
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(python_exe), "-I", "-B", "-c", code],
        cwd=str(python_exe.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise BuildError(f"Portable Python dependency validation failed: {detail}")


def _prepare_runtime(root: Path) -> Path:
    runtime = root / "packaging" / "windows" / "runtime"
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise BuildError(f"Project Python is missing: {python}")
    _run(
        [
            str(python),
            "-B",
            str(root / "packaging" / "windows" / "prepare_runtime.py"),
            "--project-root",
            str(root),
            "--destination",
            str(runtime),
            "--require-cuda",
        ],
        cwd=root,
        timeout=1800,
    )
    return runtime


def _build_electron_unpacked(root: Path, destination: Path) -> Path:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise BuildError("npm was not found.")
    if not (root / "desktop" / "node_modules" / "electron" / "package.json").is_file():
        raise BuildError("desktop/node_modules is missing. Run npm install in desktop/.")
    _run(
        [
            npm,
            "run",
            "pack:win",
            "--",
            f"--config.directories.output={destination}",
            "--config.extraMetadata.cinesubBuildFlavor=portable",
        ],
        cwd=root / "desktop",
        timeout=3600,
    )
    unpacked = destination / "win-unpacked"
    if not (unpacked / "CineSubStudio.exe").is_file():
        raise BuildError(f"Electron unpacked executable was not generated: {unpacked}")
    return unpacked


def _portable_readme(version: str) -> str:
    return f"""智译字幕工坊 / CineSub Studio {version}

启动：
1. 完整解压 ZIP，不要在压缩包内直接运行。
2. 双击 CineSubStudio.exe。

本目录是免安装便携版，不依赖系统 Python、FFmpeg 或 PATH。
small 语音识别模型已内置，可离线使用自动检测、固定单语言和多语言模式。
CUDA 运行库不包含 NVIDIA 显卡驱动；环境不兼容时应用会回退 CPU。
large-v3 未随包提供，可在运行后导入到 data\\models\\。

配置、API Key、缓存、模型、日志和字幕产物全部保存在同级 data\\ 目录。
移动应用时请连同整个目录一起移动。应用未进行代码签名，Windows 可能显示来源提示。
前端翻译提示词入口处于冻结状态，已有配置和后端接口仍然兼容。
"""


def _copy_model(snapshot: Path, destination: Path) -> None:
    shutil.copytree(
        snapshot,
        destination,
        ignore=lambda _directory, names: {
            name for name in names if name.endswith(".incomplete")
        },
    )


def _stage_portable(
    *,
    root: Path,
    unpacked: Path,
    staging_parent: Path,
    release_name: str,
    model_snapshot: Path,
    version: str,
) -> Path:
    portable_root = staging_parent / release_name
    shutil.copytree(unpacked, portable_root)
    data_root = portable_root / "data"
    for dirname in WRITABLE_DATA_DIRS:
        (data_root / dirname).mkdir(parents=True, exist_ok=True)
    _copy_model(model_snapshot, data_root / "models" / MODEL_DIRECTORY)
    (portable_root / "使用说明.txt").write_text(
        _portable_readme(version), encoding="utf-8"
    )
    notices = root / "packaging" / "windows" / "THIRD_PARTY_NOTICES.md"
    shutil.copy2(notices, portable_root / "THIRD_PARTY_NOTICES.md")
    return portable_root


def _iter_payload_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _write_package_metadata(portable_root: Path, version: str, *, cuda: bool) -> None:
    manifest_path = portable_root / "release_manifest.json"
    checksums_path = portable_root / "release_checksums.sha256"
    for generated in (manifest_path, checksums_path):
        generated.unlink(missing_ok=True)
    files = _iter_payload_files(portable_root)
    manifest = {
        "product": "CineSub Studio",
        "version": version,
        "layout": "electron-portable",
        "entrypoint": "CineSubStudio.exe",
        "data_root": "data",
        "bundled_models": ["small"],
        "cuda_bundled": cuda,
        "file_count": len(files),
        "payload_bytes": sum(path.stat().st_size for path in files),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_lines = [
        f"{_sha256(path)}  {path.relative_to(portable_root).as_posix()}"
        for path in _iter_payload_files(portable_root)
        if path != checksums_path
    ]
    checksums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def _scan_package(portable_root: Path) -> None:
    errors: list[str] = []
    for path in portable_root.rglob("*"):
        relative = path.relative_to(portable_root)
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & FORBIDDEN_PACKAGE_PARTS:
            errors.append(f"forbidden directory: {relative}")
        if path.is_file() and path.name.lower() in FORBIDDEN_PACKAGE_NAMES:
            errors.append(f"forbidden file: {relative}")
        if path.is_file() and "large-v3" in relative.as_posix().lower():
            errors.append(f"large-v3 must not be bundled: {relative}")
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        if path.suffix.lower() not in {
            ".json",
            ".txt",
            ".md",
            ".py",
            ".js",
            ".html",
            ".yml",
            ".yaml",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            errors.append(f"possible secret in: {relative}")
    if errors:
        raise BuildError("Release scan failed:\n" + "\n".join(errors[:50]))


def _write_zip(source_parent: Path, top_name: str, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    source_root = source_parent / top_name
    with ZipFile(
        destination,
        "w",
        compression=ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_parent).as_posix())


def _write_cuda_addon(
    *,
    cuda_source: Path,
    staging_parent: Path,
    release_name: str,
    destination: Path,
) -> None:
    addon_parent = staging_parent / "cuda-addon"
    addon_root = (
        addon_parent
        / release_name
        / "resources"
        / "app"
        / "tools"
        / "cuda"
    )
    shutil.copytree(cuda_source, addon_root)
    _write_zip(addon_parent, release_name, destination)


def build_portable_release(
    *,
    repo_root: Path | str = REPO_ROOT,
    version: str = DEFAULT_VERSION,
    electron_unpacked: Path | str | None = None,
    github_asset_limit: int = GITHUB_ASSET_LIMIT,
) -> BuildResult:
    root = Path(repo_root).resolve()
    if version != (root / "VERSION").read_text(encoding="utf-8").strip():
        raise BuildError("Requested version does not match VERSION.")
    release_name = _release_name(version)
    model_snapshot = _validate_inputs(root)
    _validate_portable_python(root)

    dist = root / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    build_root = root / ".tmp" / "electron-portable-build"
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)
    runtime = root / "packaging" / "windows" / "runtime"

    try:
        _prepare_runtime(root)
        if electron_unpacked is None:
            unpacked = _build_electron_unpacked(root, build_root / "electron")
        else:
            unpacked = Path(electron_unpacked).resolve()
            if not (unpacked / "CineSubStudio.exe").is_file():
                raise BuildError(f"Invalid Electron unpacked directory: {unpacked}")

        staging_parent = build_root / "staging"
        portable_root = _stage_portable(
            root=root,
            unpacked=unpacked,
            staging_parent=staging_parent,
            release_name=release_name,
            model_snapshot=model_snapshot,
            version=version,
        )
        cuda_root = portable_root / "resources" / "app" / "tools" / "cuda"
        if not cuda_root.is_dir():
            raise BuildError("Electron payload does not contain the staged CUDA runtime.")

        _write_package_metadata(portable_root, version, cuda=True)
        _scan_package(portable_root)
        zip_path = dist / f"{release_name}.zip"
        _write_zip(staging_parent, release_name, zip_path)

        split_cuda = zip_path.stat().st_size >= github_asset_limit
        cuda_addon_path: Path | None = None
        cuda_addon_sha256: Path | None = None
        if split_cuda:
            cuda_addon_path = (
                dist / f"{PRODUCT_SLUG}-{version}-windows-x64-cuda-addon.zip"
            )
            _write_cuda_addon(
                cuda_source=cuda_root,
                staging_parent=build_root,
                release_name=release_name,
                destination=cuda_addon_path,
            )
            shutil.rmtree(cuda_root)
            _write_package_metadata(portable_root, version, cuda=False)
            _scan_package(portable_root)
            _write_zip(staging_parent, release_name, zip_path)
            if zip_path.stat().st_size >= github_asset_limit:
                raise BuildError(
                    "CPU portable ZIP still exceeds the GitHub 2 GiB asset limit."
                )
            if cuda_addon_path.stat().st_size >= github_asset_limit:
                raise BuildError("CUDA add-on ZIP exceeds the GitHub 2 GiB asset limit.")
            cuda_addon_sha256 = _write_sha256_sidecar(cuda_addon_path)

        zip_sidecar = _write_sha256_sidecar(zip_path)
        return BuildResult(
            release_name=release_name,
            zip_path=zip_path,
            zip_sha256_path=zip_sidecar,
            cuda_addon_path=cuda_addon_path,
            cuda_addon_sha256_path=cuda_addon_sha256,
            split_cuda=split_cuda,
            zip_bytes=zip_path.stat().st_size,
        )
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
        shutil.rmtree(runtime, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the frozen CineSub Studio 0.6.2 Electron portable ZIP."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--electron-unpacked", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_portable_release(
            repo_root=args.repo_root,
            version=args.version,
            electron_unpacked=args.electron_unpacked,
        )
    except (BuildError, OSError, subprocess.SubprocessError) as exc:
        print(f"[release] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"[release] ZIP: {result.zip_path}")
    print(f"[release] SHA256: {result.zip_sha256_path}")
    if result.cuda_addon_path:
        print(f"[release] CUDA add-on: {result.cuda_addon_path}")
        print(f"[release] CUDA SHA256: {result.cuda_addon_sha256_path}")
    print(f"[release] Split CUDA: {result.split_cuda}")
    print(f"[release] ZIP bytes: {result.zip_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
