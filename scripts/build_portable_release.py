from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_NAME = "cinesub-portable"
DEFAULT_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
RELEASE_MARKER = ".portable-layout"
MANIFEST_NAME = "release_manifest.json"
REPORT_NAME = "release_report.md"
CHECKSUMS_NAME = "release_checksums.sha256"
BUILDER_MILESTONE = "m6.7-release-candidate-packaging"
LARGE_FILE_THRESHOLD_BYTES = 5 * 1024 * 1024
LARGE_FILE_LIMIT = 20
RUNTIME_DIRS = (
    "config",
    "input",
    "output",
    "work",
    "logs",
    "uploads",
    "models",
    ".cache",
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
    "tests",
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
LOCAL_CONFIG_FILENAMES = {
    "providers.local.json",
    "language_profiles.local.json",
}
REPO_CONTROL_FILENAMES = {
    ".gitignore",
    ".gitattributes",
}
SECRET_SENTINEL_FRAGMENTS = (
    ("sk-test-", "M5-SECRET-SHOULD-NOT-LEAK"),
)
SK_STYLE_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")
BEARER_SECRET_RE = re.compile(r"(?i)\b(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/=-]{20,}")
CONFIG_SECRET_RE = re.compile(
    r"""(?ix)
    ["']?(?:api_key|access_token|refresh_token|client_secret|password)["']?
    \s*[:=]\s*
    ["']([^"'\s]{12,})["']
    """
)


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    output_dir: Path
    app_dir: Path
    python_runtime_dir: Path
    ffmpeg_copied: bool
    manifest_path: Path
    report_path: Path
    checksums_path: Path
    zip_path: Path | None
    zip_sha256_path: Path | None
    copied_file_count: int
    total_bytes: int


def build_portable_release(
    *,
    repo_root: Path | str | None = None,
    output: Path | str | None = None,
    python_runtime: Path | str | None = None,
    version: str = DEFAULT_VERSION,
    make_zip: bool = False,
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

    summary = _collect_release_summary(output_dir)
    excluded_summary = _collect_excluded_summary(root)
    initial_scan = _scan_release_for_leaks(output_dir)
    if initial_scan:
        raise BuildError("Release leak scan failed:\n" + "\n".join(f"- {issue}" for issue in initial_scan))

    manifest_path = output_dir / MANIFEST_NAME
    report_path = output_dir / REPORT_NAME
    checksums_path = output_dir / CHECKSUMS_NAME
    manifest = _build_manifest(
        version=version,
        summary=summary,
        excluded_summary=excluded_summary,
        ffmpeg_copied=ffmpeg_copied,
    )
    _write_manifest(manifest_path, manifest)
    _write_report(report_path, manifest)
    checksum_summary = _write_checksums(checksums_path, output_dir)
    _update_checksum_summary(manifest_path, report_path, checksum_summary)

    final_scan = _scan_release_for_leaks(output_dir)
    if final_scan:
        raise BuildError("Release leak scan failed:\n" + "\n".join(f"- {issue}" for issue in final_scan))

    zip_path = None
    zip_sha256_path = None
    if make_zip:
        zip_path, zip_sha256_path = _write_zip_package(output_dir, version)

    return BuildResult(
        output_dir=output_dir,
        app_dir=app_dir,
        python_runtime_dir=runtime_python_dir,
        ffmpeg_copied=ffmpeg_copied,
        manifest_path=manifest_path,
        report_path=report_path,
        checksums_path=checksums_path,
        zip_path=zip_path,
        zip_sha256_path=zip_sha256_path,
        copied_file_count=summary["file_count"],
        total_bytes=summary["total_bytes"],
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
    if name in LOCAL_CONFIG_FILENAMES or name in REPO_CONTROL_FILENAMES:
        return True
    if any(name.endswith(suffix) for suffix in EXCLUDED_FILE_SUFFIXES):
        return True
    if name == "review_needed.srt" or name.endswith(".review_needed.srt"):
        return True
    return False


def _collect_release_summary(output_dir: Path) -> dict[str, object]:
    file_entries: list[dict[str, object]] = []
    total_bytes = 0
    top_level: dict[str, int] = {}
    generated = {MANIFEST_NAME, REPORT_NAME, CHECKSUMS_NAME}
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name in generated:
            continue
        relative = _release_relative(path, output_dir)
        size = path.stat().st_size
        total_bytes += size
        top_name = relative.split("/", 1)[0]
        top_level[top_name] = top_level.get(top_name, 0) + 1
        file_entries.append({"path": relative, "bytes": size})

    largest = sorted(file_entries, key=lambda item: (-int(item["bytes"]), str(item["path"])))[:LARGE_FILE_LIMIT]
    large_files = [item for item in largest if int(item["bytes"]) >= LARGE_FILE_THRESHOLD_BYTES]
    if not large_files:
        large_files = largest[: min(5, len(largest))]

    return {
        "file_count": len(file_entries),
        "total_bytes": total_bytes,
        "largest_files": large_files,
        "top_level_file_counts": dict(sorted(top_level.items())),
    }


def _collect_excluded_summary(repo_root: Path) -> dict[str, object]:
    top_level: dict[str, str] = {}
    for name in sorted(EXCLUDED_DIR_NAMES):
        if (repo_root / name).exists():
            top_level[name] = "excluded"
    if (repo_root / "tests").exists():
        top_level["tests"] = "not included in portable app"
    for name in ("tools/python", "tools/wheelhouse", "tools/cuda"):
        if (repo_root / name).exists():
            top_level[name] = "excluded runtime layer"

    app_artifacts: dict[str, int] = {}
    for dirname in APP_DIRS:
        source = repo_root / dirname
        if not source.exists():
            continue
        for path in source.rglob("*"):
            if path.is_dir() and path.name in EXCLUDED_DIR_NAMES:
                app_artifacts[path.name] = app_artifacts.get(path.name, 0) + 1
            elif path.is_file() and _is_excluded_file(path):
                suffix = _excluded_file_category(path)
                app_artifacts[suffix] = app_artifacts.get(suffix, 0) + 1

    return {
        "top_level": top_level,
        "app_artifacts": dict(sorted(app_artifacts.items())),
    }


def _excluded_file_category(path: Path) -> str:
    name = path.name
    if name in LOCAL_CONFIG_FILENAMES:
        return "local config"
    if name in REPO_CONTROL_FILENAMES:
        return "repo control"
    for suffix in sorted(EXCLUDED_FILE_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix):
            return suffix
    if name == "review_needed.srt" or name.endswith(".review_needed.srt"):
        return "review_needed.srt"
    return "excluded file"


def _build_manifest(
    *,
    version: str,
    summary: dict[str, object],
    excluded_summary: dict[str, object],
    ffmpeg_copied: bool,
) -> dict[str, object]:
    return {
        "builder": BUILDER_MILESTONE,
        "version": version,
        "paths": {
            "output_root": ".",
            "app_root": "app",
            "runtime_python_root": "runtime/python",
            "checksums": CHECKSUMS_NAME,
        },
        "copied_file_count": summary["file_count"],
        "payload_file_count": summary["file_count"],
        "total_bytes": summary["total_bytes"],
        "payload_total_bytes": summary["total_bytes"],
        "largest_files": summary["largest_files"],
        "top_level_file_counts": summary["top_level_file_counts"],
        "excluded_summary": excluded_summary,
        "generated_files": [MANIFEST_NAME, REPORT_NAME, CHECKSUMS_NAME],
        "ffmpeg_copied": ffmpeg_copied,
        "leak_scan": {
            "status": "passed",
            "checks": [
                "path/name artifact scan",
                "secret-looking value scan",
                "release-relative report paths",
            ],
        },
        "checksums": {
            "path": CHECKSUMS_NAME,
            "covers": "release payload files, excluding generated release metadata",
        },
        "notes": [
            "Generated from a local source checkout; absolute source paths are intentionally omitted.",
            "Zip package byte size and SHA256 are written outside the zip to avoid checksum cycles.",
            "This release candidate does not include tests, sample media, subtitles, models, wheelhouse, or CUDA.",
        ],
    }


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_report(path: Path, manifest: dict[str, object]) -> None:
    largest_files = manifest["largest_files"]
    excluded = manifest["excluded_summary"]
    top_level = manifest["top_level_file_counts"]
    lines = [
        "# CineSub Portable Release Report",
        "",
        f"- Builder: `{manifest['builder']}`",
        f"- Version: `{manifest['version']}`",
        f"- Payload files: `{manifest['payload_file_count']}`",
        f"- Payload size: `{_format_bytes(int(manifest['payload_total_bytes']))}`",
        f"- FFmpeg copied: `{'yes' if manifest['ffmpeg_copied'] else 'no'}`",
        f"- Leak scan: `{manifest['leak_scan']['status']}`",
        f"- Checksums: `{CHECKSUMS_NAME}`",
        "",
        "Generated from a local source checkout; absolute source paths are intentionally omitted.",
        "Zip package byte size and SHA256 are written outside the zip to avoid checksum cycles.",
        "",
        "## Release Paths",
        "",
        "- Output root: `.`",
        "- App root: `app`",
        "- Runtime Python root: `runtime/python`",
        f"- Checksums: `{CHECKSUMS_NAME}`",
        "",
        "## Top-Level File Counts",
        "",
    ]
    if top_level:
        for name, count in top_level.items():
            lines.append(f"- `{name}`: `{count}`")
    else:
        lines.append("- No files copied.")

    lines.extend(["", "## Largest Files", ""])
    if largest_files:
        for item in largest_files:
            lines.append(f"- `{item['path']}`: `{_format_bytes(int(item['bytes']))}`")
    else:
        lines.append("- No files copied.")

    lines.extend(["", "## Excluded Categories", ""])
    top_excluded = excluded.get("top_level", {}) if isinstance(excluded, dict) else {}
    app_artifacts = excluded.get("app_artifacts", {}) if isinstance(excluded, dict) else {}
    if top_excluded:
        for name, reason in top_excluded.items():
            lines.append(f"- `{name}`: {reason}")
    if app_artifacts:
        for name, count in app_artifacts.items():
            lines.append(f"- `{name}` app artifacts: `{count}`")
    if not top_excluded and not app_artifacts:
        lines.append("- No excluded categories were present in the source checkout.")

    lines.extend(
        [
            "",
            "## Remaining Large Dependency Layers",
            "",
            "- Portable Python runtime is copied only when provided locally.",
            "- Models, wheelhouse, CUDA, and release archives are not generated by M6.4.",
            "- Empty runtime placeholders are allowed for output, work, logs, cache, models, and uploads.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def _scan_release_for_leaks(output_dir: Path) -> list[str]:
    issues: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        relative = _release_relative(path, output_dir)
        parts = Path(relative).parts
        if path.is_dir():
            if _is_rejected_release_dir(parts):
                issues.append(f"rejected directory: {relative}")
            continue
        if _is_rejected_release_file(path, parts):
            issues.append(f"rejected file: {relative}")
            continue
        if not _should_scan_release_file_content(parts):
            continue
        issues.extend(f"{relative}: {issue}" for issue in _scan_file_content(path))
    return issues


def _is_rejected_release_dir(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    if ".git" in parts or ".venv" in parts or "dist" in parts:
        return True
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "tools" and parts[2] == "python":
        return True
    return False


def _is_rejected_release_file(path: Path, parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    name = path.name
    if name in {MANIFEST_NAME, REPORT_NAME, CHECKSUMS_NAME, RELEASE_MARKER, "start_app.bat"}:
        return False
    if name in LOCAL_CONFIG_FILENAMES or name in REPO_CONTROL_FILENAMES:
        return True
    if ".git" in parts or ".venv" in parts or "dist" in parts:
        return True
    if len(parts) >= 2 and parts[0] == "runtime" and parts[1] == "python":
        return False
    if len(parts) >= 2 and parts[0] == "tools" and parts[1] == "ffmpeg":
        return False
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "tools" and parts[2] == "python":
        return True
    if parts[0] in {"output", "work", "logs", ".cache", "models", "uploads"}:
        return True
    if _is_excluded_file(path):
        return True
    return False


def _should_scan_release_file_content(parts: tuple[str, ...]) -> bool:
    if len(parts) >= 2 and parts[0] == "runtime" and parts[1] == "python":
        return False
    return True


def _scan_file_content(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"could not read for leak scan: {exc}"]
    if b"\x00" in data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return []

    issues: list[str] = []
    for fragments in SECRET_SENTINEL_FRAGMENTS:
        if "".join(fragments) in text:
            issues.append("known secret sentinel value")
    if SK_STYLE_SECRET_RE.search(text):
        issues.append("sk-style API key value")
    if BEARER_SECRET_RE.search(text):
        issues.append("bearer token value")
    for match in CONFIG_SECRET_RE.finditer(text):
        value = match.group(1)
        if _looks_like_secret_value(value):
            issues.append("secret-looking config value")
            break
    return issues


def _looks_like_secret_value(value: str) -> bool:
    if len(value) < 12:
        return False
    lowered = value.lower()
    if lowered in {"placeholder", "changeme", "example", "not-set", "none", "null"}:
        return False
    if re.fullmatch(r"[A-Za-z0-9._~+/=-]{12,}", value):
        return True
    return False


def _release_relative(path: Path, output_dir: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def _write_checksums(path: Path, output_dir: Path) -> dict[str, object]:
    entries: list[str] = []
    total_bytes = 0
    for file_path in sorted(output_dir.rglob("*")):
        if not file_path.is_file() or file_path == path:
            continue
        if file_path.name in {MANIFEST_NAME, REPORT_NAME, CHECKSUMS_NAME}:
            continue
        relative = _release_relative(file_path, output_dir)
        digest = _sha256_file(file_path)
        total_bytes += file_path.stat().st_size
        entries.append(f"{digest}  {relative}")
    path.write_text("\n".join(entries) + ("\n" if entries else ""), encoding="utf-8", newline="\n")
    return {
        "path": CHECKSUMS_NAME,
        "covered_file_count": len(entries),
        "covered_total_bytes": total_bytes,
    }


def _update_checksum_summary(
    manifest_path: Path,
    report_path: Path,
    checksum_summary: dict[str, object],
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["checksums"].update(checksum_summary)
    _write_manifest(manifest_path, manifest)
    _write_report(report_path, manifest)


def _write_zip_package(output_dir: Path, version: str) -> tuple[Path, Path]:
    dist_root = output_dir.parent
    zip_path = dist_root / f"{output_dir.name}-{version}.zip"
    zip_sha256_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    if zip_path.exists():
        zip_path.unlink()
    if zip_sha256_path.exists():
        zip_sha256_path.unlink()

    archive_root = output_dir.name
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            archive_name = Path(archive_root) / path.relative_to(output_dir)
            archive.write(path, archive_name.as_posix())

    digest = _sha256_file(zip_path)
    zip_sha256_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8", newline="\n")
    return zip_path, zip_sha256_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--zip", action="store_true", dest="make_zip")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = build_portable_release(
            output=args.output,
            python_runtime=args.python_runtime,
            version=args.version,
            make_zip=args.make_zip,
            force=args.force,
        )
    except BuildError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"Portable release prototype: {result.output_dir}")
    print(f"App files: {result.app_dir}")
    print(f"Python runtime: {result.python_runtime_dir}")
    print(f"FFmpeg copied: {'yes' if result.ffmpeg_copied else 'no'}")
    print(f"Release manifest: {result.manifest_path}")
    print(f"Release report: {result.report_path}")
    print(f"Release checksums: {result.checksums_path}")
    if result.zip_path and result.zip_sha256_path:
        print(f"Release zip: {result.zip_path}")
        print(f"Release zip SHA256: {result.zip_sha256_path}")
    print(f"Copied files: {result.copied_file_count}")
    print(f"Total bytes: {result.total_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
