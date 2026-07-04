from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CATEGORY_CHOICES = (
    "tmp",
    "dist",
    "cache",
    "logs",
    "portable-python",
    "pycache",
    "outputs",
)

PROTECTED_ROOTS = {
    ".git",
    ".venv",
    "src",
    "web",
    "tests",
    "scripts",
    "acceptance",
    "config",
}
PROTECTED_ROOT_FILES = {
    "project_evaluation_report.md",
    "README.md",
    "TRIAL.md",
    "requirements.txt",
}
PROTECTED_CONFIG_FILES = {
    "providers.local.json",
    "language_profiles.local.json",
}
PY_CACHE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}
USER_DATA_ROOTS = {
    "archive",
    "failed",
    "input",
    "models",
    "output",
    "reports",
    "uploads",
    "work",
}
SAFE_TMP_ROOTS = {
    ".tmp",
}
SAFE_CACHE_ROOTS = {
    ".cache/pip",
    ".cache/huggingface",
}
SAFE_PORTABLE_PYTHON_ROOTS = {
    "tools/python",
}
GENERATED_DIST_DIRS = {
    "dist/cinesub-portable",
}
RELEASE_ARCHIVE_SUFFIXES = (
    ".zip",
    ".zip.sha256",
)
DEFAULT_LOG_OLDER_THAN_DAYS = 14


@dataclass(frozen=True)
class Candidate:
    path: Path
    relative: str
    category: str
    reason: str
    bytes: int
    requires_user_data_confirmation: bool = False
    requires_release_archive_confirmation: bool = False


@dataclass(frozen=True)
class CleanupPlan:
    repo_root: Path
    candidates: list[Candidate]
    skipped: list[str]
    tracked_ok: bool
    tracked_warning: str | None
    total_bytes: int
    top_level_sizes: dict[str, int]


def build_cleanup_plan(
    *,
    repo_root: Path,
    categories: set[str] | None = None,
    paths: list[str] | None = None,
    include_release_archives: bool = False,
    older_than_days: int = DEFAULT_LOG_OLDER_THAN_DAYS,
    tracked_files: set[str] | None = None,
    tracked_ok: bool | None = None,
    tracked_warning: str | None = None,
) -> CleanupPlan:
    root = repo_root.resolve()
    selected = set(categories or CATEGORY_CHOICES)
    explicit_paths = paths or []
    skipped: list[str] = []

    if tracked_ok is None:
        detected, warning = _git_tracked_files(root)
        tracked_files = detected
        tracked_ok = warning is None
        tracked_warning = warning
    elif tracked_files is None:
        tracked_files = set()

    top_level_sizes = _top_level_sizes(root)
    candidates: list[Candidate] = []

    if explicit_paths:
        for value in explicit_paths:
            try:
                candidate = _candidate_for_explicit_path(
                    root=root,
                    value=value,
                    categories=selected,
                    include_release_archives=include_release_archives,
                    tracked_files=tracked_files,
                    older_than_days=older_than_days,
                )
            except ValueError as exc:
                skipped.append(str(exc))
                continue
            if candidate is None:
                skipped.append(f"{value}: no cleanup candidate matched")
            else:
                candidates.append(candidate)
    else:
        if "tmp" in selected:
            candidates.extend(
                _candidate_if_allowed(
                    root=root,
                    path=root / name,
                    category="tmp",
                    reason="temporary workspace artifacts",
                    tracked_files=tracked_files,
                )
                for name in sorted(SAFE_TMP_ROOTS)
            )
        if "pycache" in selected:
            candidates.extend(
                _candidate_if_allowed(
                    root=root,
                    path=path,
                    category="pycache",
                    reason="Python/tool cache directory",
                    tracked_files=tracked_files,
                )
                for path in _iter_cache_dirs(root)
            )
            for name in sorted(path.name for path in root.glob("pytest-cache-files-*") if path.is_dir()):
                candidates.append(
                    _candidate_if_allowed(
                        root=root,
                        path=root / name,
                        category="pycache",
                        reason="pytest temporary cache directory",
                        tracked_files=tracked_files,
                    )
                )
        if "dist" in selected:
            candidates.extend(
                _candidate_if_allowed(
                    root=root,
                    path=root / name,
                    category="dist",
                    reason="generated portable release directory",
                    tracked_files=tracked_files,
                )
                for name in sorted(GENERATED_DIST_DIRS)
            )
            if include_release_archives:
                candidates.extend(_release_archive_candidates(root, tracked_files))
        if "cache" in selected:
            candidates.extend(
                _candidate_if_allowed(
                    root=root,
                    path=root / name,
                    category="cache",
                    reason="project-local dependency/model cache",
                    tracked_files=tracked_files,
                )
                for name in sorted(SAFE_CACHE_ROOTS)
            )
        if "portable-python" in selected:
            candidates.extend(
                _candidate_if_allowed(
                    root=root,
                    path=root / name,
                    category="portable-python",
                    reason="rebuildable portable Python runtime",
                    tracked_files=tracked_files,
                )
                for name in sorted(SAFE_PORTABLE_PYTHON_ROOTS)
            )
        if "logs" in selected:
            candidates.extend(_log_candidates(root, tracked_files, older_than_days))
        if "outputs" in selected:
            candidates.extend(
                _candidate_if_allowed(
                    root=root,
                    path=root / name,
                    category="outputs",
                    reason="user data/output artifact root",
                    tracked_files=tracked_files,
                    requires_user_data_confirmation=True,
                )
                for name in sorted(USER_DATA_ROOTS)
            )

    clean_candidates = _dedupe_candidates(
        candidate for candidate in candidates if candidate is not None
    )
    total_bytes = sum(candidate.bytes for candidate in clean_candidates)
    return CleanupPlan(
        repo_root=root,
        candidates=clean_candidates,
        skipped=skipped,
        tracked_ok=bool(tracked_ok),
        tracked_warning=tracked_warning,
        total_bytes=total_bytes,
        top_level_sizes=top_level_sizes,
    )


def apply_cleanup_plan(
    plan: CleanupPlan,
    *,
    confirm_user_data: bool = False,
) -> tuple[int, list[str]]:
    messages: list[str] = []
    if not plan.tracked_ok:
        return 2, ["tracked-file detection failed; deletion is disabled"]

    needs_user_data_confirmation = [
        candidate.relative
        for candidate in plan.candidates
        if candidate.requires_user_data_confirmation
    ]
    if needs_user_data_confirmation and not confirm_user_data:
        return 2, [
            "user-data candidates require --confirm-user-data: "
            + ", ".join(needs_user_data_confirmation)
        ]

    for candidate in sorted(plan.candidates, key=lambda item: item.relative, reverse=True):
        try:
            if candidate.path.is_dir():
                shutil.rmtree(candidate.path)
            elif candidate.path.exists():
                candidate.path.unlink()
            messages.append(f"deleted {candidate.relative}")
        except OSError as exc:
            return 1, [*messages, f"failed to delete {candidate.relative}: {exc}"]
    return 0, messages


def _git_tracked_files(root: Path) -> tuple[set[str], str | None]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return set(), f"git ls-files failed: {exc}"
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return set(), f"git ls-files failed with exit code {result.returncode}: {stderr}"
    text = result.stdout.decode("utf-8", errors="replace")
    return {item.replace("\\", "/") for item in text.split("\0") if item}, None


def _candidate_for_explicit_path(
    *,
    root: Path,
    value: str,
    categories: set[str],
    include_release_archives: bool,
    tracked_files: set[str],
    older_than_days: int,
) -> Candidate | None:
    path = (root / value).resolve()
    try:
        relative = _relative(path, root)
    except ValueError as exc:
        raise ValueError(f"{value}: refusing path outside project root") from exc
    if not path.exists():
        return None
    if _is_explicitly_protected_target(relative, path):
        raise ValueError(f"{relative}: refusing protected path")
    category = _category_for_path(relative, path)
    if category is None or category not in categories:
        return None
    requires_release = _is_release_archive(relative, path)
    if requires_release and not include_release_archives:
        raise ValueError(f"{relative}: release archives require --include-release-archives")
    if category == "logs" and path.is_file() and not _is_old_enough(path, older_than_days):
        return None
    return _candidate_if_allowed(
        root=root,
        path=path,
        category=category,
        reason=_reason_for_category(category),
        tracked_files=tracked_files,
        requires_user_data_confirmation=category == "outputs",
        requires_release_archive_confirmation=requires_release,
    )


def _category_for_path(relative: str, path: Path) -> str | None:
    first = relative.split("/", 1)[0]
    if relative in SAFE_TMP_ROOTS or relative.startswith(".tmp/"):
        return "tmp"
    if _is_pycache_path(path):
        return "pycache"
    if relative in GENERATED_DIST_DIRS or relative.startswith("dist/cinesub-portable/"):
        return "dist"
    if _is_release_archive(relative, path):
        return "dist"
    if relative in SAFE_CACHE_ROOTS or any(relative.startswith(f"{name}/") for name in SAFE_CACHE_ROOTS):
        return "cache"
    if relative in SAFE_PORTABLE_PYTHON_ROOTS or relative.startswith("tools/python/"):
        return "portable-python"
    if first == "logs" and path.is_file() and path.suffix.lower() == ".log":
        return "logs"
    if "/" not in relative and path.is_file() and path.suffix.lower() == ".log":
        return "logs"
    if first in USER_DATA_ROOTS:
        return "outputs"
    return None


def _reason_for_category(category: str) -> str:
    return {
        "tmp": "temporary workspace artifacts",
        "dist": "generated release artifact",
        "cache": "project-local dependency/model cache",
        "logs": "old generated log file",
        "portable-python": "rebuildable portable Python runtime",
        "pycache": "Python/tool cache directory",
        "outputs": "user data/output artifact root",
    }[category]


def _candidate_if_allowed(
    *,
    root: Path,
    path: Path,
    category: str,
    reason: str,
    tracked_files: set[str],
    requires_user_data_confirmation: bool = False,
    requires_release_archive_confirmation: bool = False,
) -> Candidate | None:
    if not path.exists():
        return None
    relative = _relative(path.resolve(), root)
    if _is_protected_path(relative, path, category):
        return None
    if _contains_tracked_file(relative, tracked_files):
        return None
    return Candidate(
        path=path.resolve(),
        relative=relative,
        category=category,
        reason=reason,
        bytes=_path_size(path),
        requires_user_data_confirmation=requires_user_data_confirmation,
        requires_release_archive_confirmation=requires_release_archive_confirmation,
    )


def _is_protected_path(relative: str, path: Path, category: str) -> bool:
    parts = Path(relative).parts
    if not parts:
        return True
    first = parts[0]
    if relative == "project_evaluation_report.md":
        return True
    if "/" not in relative and (
        relative in PROTECTED_ROOT_FILES
        or relative.startswith("requirements")
        and path.suffix.lower() == ".txt"
    ):
        return True
    if first == "config":
        return True
    if path.name in PROTECTED_CONFIG_FILES:
        return True
    if first in PROTECTED_ROOTS:
        return not (category == "pycache" and _is_pycache_path(path))
    return False


def _is_explicitly_protected_target(relative: str, path: Path) -> bool:
    parts = Path(relative).parts
    if not parts:
        return True
    first = parts[0]
    if relative == "project_evaluation_report.md":
        return True
    if first == "config":
        return True
    if "/" not in relative and (
        relative in PROTECTED_ROOT_FILES
        or relative.startswith("requirements")
        and path.suffix.lower() == ".txt"
    ):
        return True
    if first in PROTECTED_ROOTS and not _is_pycache_path(path):
        return True
    return False


def _contains_tracked_file(relative: str, tracked_files: set[str]) -> bool:
    normalized = relative.replace("\\", "/").strip("/")
    if normalized in tracked_files:
        return True
    prefix = normalized + "/"
    return any(path.startswith(prefix) for path in tracked_files)


def _iter_cache_dirs(root: Path) -> list[Path]:
    result: list[Path] = []
    pruned_roots = {
        ".git",
        ".venv",
        ".cache",
        ".tmp",
        "archive",
        "dist",
        "failed",
        "input",
        "logs",
        "models",
        "output",
        "reports",
        "tools",
        "uploads",
        "work",
    }
    for current, dirnames, _filenames in os.walk(root):
        current_path = Path(current)
        if current_path == root:
            dirnames[:] = [name for name in dirnames if name not in pruned_roots]
        for dirname in list(dirnames):
            if dirname in PY_CACHE_DIR_NAMES or dirname.startswith("pytest-cache-files-"):
                result.append(current_path / dirname)
                dirnames.remove(dirname)
    return result


def _release_archive_candidates(root: Path, tracked_files: set[str]) -> list[Candidate]:
    dist = root / "dist"
    if not dist.is_dir():
        return []
    candidates: list[Candidate] = []
    for path in sorted(dist.iterdir()):
        relative = _relative(path.resolve(), root)
        if path.is_file() and _is_release_archive(relative, path):
            candidate = _candidate_if_allowed(
                root=root,
                path=path,
                category="dist",
                reason="release archive",
                tracked_files=tracked_files,
                requires_release_archive_confirmation=True,
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _log_candidates(root: Path, tracked_files: set[str], older_than_days: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    paths: list[Path] = []
    logs_dir = root / "logs"
    if logs_dir.is_dir():
        paths.extend(path for path in logs_dir.rglob("*.log") if path.is_file())
    paths.extend(path for path in root.glob("*.log") if path.is_file())
    for path in sorted(paths):
        if not _is_old_enough(path, older_than_days):
            continue
        candidate = _candidate_if_allowed(
            root=root,
            path=path,
            category="logs",
            reason=f"generated log file older than {older_than_days} days",
            tracked_files=tracked_files,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _is_old_enough(path: Path, older_than_days: int) -> bool:
    if older_than_days <= 0:
        return True
    import time

    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds >= older_than_days * 24 * 60 * 60


def _is_pycache_path(path: Path) -> bool:
    return path.is_dir() and path.name in PY_CACHE_DIR_NAMES or (
        path.is_dir() and path.name.startswith("pytest-cache-files-")
    )


def _is_release_archive(relative: str, path: Path) -> bool:
    if not path.is_file():
        return False
    if not relative.startswith("dist/"):
        return False
    return relative.endswith(RELEASE_ARCHIVE_SUFFIXES)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _top_level_sizes(root: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for path in root.iterdir():
        result[path.name] = _path_size(path)
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def _dedupe_candidates(candidates: list[Candidate] | object) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda item: (item.relative.count("/"), item.relative))
    kept: list[Candidate] = []
    for candidate in ordered:
        if any(candidate.relative == item.relative or candidate.relative.startswith(item.relative + "/") for item in kept):
            continue
        kept.append(candidate)
    return kept


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


def _render_text(plan: CleanupPlan, *, apply: bool) -> str:
    lines = [
        "CineSub local artifact cleanup",
        f"Mode: {'apply' if apply else 'dry-run'}",
        f"Project: {plan.repo_root}",
        "",
        "Top-level disk usage:",
    ]
    for name, size in plan.top_level_sizes.items():
        lines.append(f"- {name}: {_format_bytes(size)}")

    lines.extend(["", "Cleanup candidates:"])
    if plan.candidates:
        for candidate in plan.candidates:
            flags: list[str] = []
            if candidate.requires_user_data_confirmation:
                flags.append("requires --confirm-user-data")
            if candidate.requires_release_archive_confirmation:
                flags.append("release archive")
            suffix = f" ({'; '.join(flags)})" if flags else ""
            lines.append(
                f"- [{candidate.category}] {candidate.relative}: "
                f"{_format_bytes(candidate.bytes)} - {candidate.reason}{suffix}"
            )
    else:
        lines.append("- none")

    if plan.skipped:
        lines.extend(["", "Skipped/refused:"])
        lines.extend(f"- {item}" for item in plan.skipped)
    if plan.tracked_warning:
        lines.extend(["", f"WARNING: {plan.tracked_warning}"])
        lines.append("Deletion is disabled because tracked-file protection could not be verified.")

    lines.extend(["", f"Candidate total: {_format_bytes(plan.total_bytes)}"])
    if not apply:
        lines.append("Dry-run only. Re-run with --apply to delete eligible candidates.")
    return "\n".join(lines)


def _render_json(plan: CleanupPlan, *, apply: bool) -> str:
    payload = {
        "mode": "apply" if apply else "dry-run",
        "project": str(plan.repo_root),
        "tracked_ok": plan.tracked_ok,
        "tracked_warning": plan.tracked_warning,
        "top_level_sizes": plan.top_level_sizes,
        "candidate_total_bytes": plan.total_bytes,
        "candidates": [
            {
                "path": candidate.relative,
                "category": candidate.category,
                "reason": candidate.reason,
                "bytes": candidate.bytes,
                "requires_user_data_confirmation": candidate.requires_user_data_confirmation,
                "requires_release_archive_confirmation": candidate.requires_release_archive_confirmation,
            }
            for candidate in plan.candidates
        ],
        "skipped": plan.skipped,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and optionally clean project-local generated artifacts."
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=CATEGORY_CHOICES,
        help="Cleanup category to include. May be repeated. Defaults to all categories for audit.",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Project-relative path to audit/clean. This narrows scope and does not bypass protections.",
    )
    parser.add_argument("--apply", action="store_true", help="Delete eligible candidates.")
    parser.add_argument(
        "--confirm-user-data",
        action="store_true",
        help="Allow deletion of user-data categories such as output/work/input/uploads/archive.",
    )
    parser.add_argument(
        "--include-release-archives",
        action="store_true",
        help="Allow release zip archives under dist/ to be selected.",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=DEFAULT_LOG_OLDER_THAN_DAYS,
        help="Minimum age for log files selected by the logs category.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON audit output.")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(args.repo_root).resolve()
    categories = set(args.category) if args.category else None
    plan = build_cleanup_plan(
        repo_root=root,
        categories=categories,
        paths=args.path,
        include_release_archives=args.include_release_archives,
        older_than_days=args.older_than_days,
    )

    print(_render_json(plan, apply=args.apply) if args.json else _render_text(plan, apply=args.apply))

    if not args.apply:
        return 0
    if not args.category and not args.path:
        print("Refusing --apply without --category or --path.", file=sys.stderr)
        return 2
    if plan.skipped:
        print("Refusing --apply because one or more requested paths were skipped/refused.", file=sys.stderr)
        return 2
    code, messages = apply_cleanup_plan(plan, confirm_user_data=args.confirm_user_data)
    stream = sys.stderr if code else sys.stdout
    for message in messages:
        print(message, file=stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
