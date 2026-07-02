from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".py",
    ".pyw",
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".json",
    ".json5",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "output",
    "work",
    "logs",
    "acceptance/screenshots",
}

EXCLUDED_FILE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".bmp",
    ".pdf",
    ".zip",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".pyd",
    ".so",
    ".mp3",
    ".mp4",
    ".mkv",
    ".mov",
    ".wav",
    ".srt",
    ".ass",
    ".vtt",
}

FORBIDDEN_SNIPPETS = [
    "\ufffd",
    "妯″瀷",
    "鎺ュ",
    "鈹€",
    "鈺愨",
    "鏃犳硶",
    "瑙ｆ瀽",
    "璺緞",
]


def _is_under_excluded_dir(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT)
    parts = relative.parts

    for index, part in enumerate(parts):
        if part in EXCLUDED_DIR_NAMES:
            return True

        joined = "/".join(parts[: index + 1])
        if joined in EXCLUDED_DIR_NAMES:
            return True

    return False


def _is_text_candidate(path: Path) -> bool:
    if not path.is_file():
        return False

    if _is_under_excluded_dir(path):
        return False

    if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return False

    if path.name == ".env.example":
        return True

    return path.suffix.lower() in TEXT_SUFFIXES


def _iter_text_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    return sorted(
        PROJECT_ROOT / line
        for line in result.stdout.splitlines()
        if line and _is_text_candidate(PROJECT_ROOT / line)
    )


def test_tracked_text_files_are_utf8_decodable() -> None:
    bad_files: list[str] = []

    for path in _iter_text_files():
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            bad_files.append(f"{path.relative_to(PROJECT_ROOT)}: {exc}")

    assert not bad_files, "Non UTF-8 text files found:\n" + "\n".join(bad_files)


def test_text_files_do_not_contain_mojibake_or_private_use_chars() -> None:
    failures: list[str] = []

    for path in _iter_text_files():
        text = path.read_text(encoding="utf-8")

        for snippet in FORBIDDEN_SNIPPETS:
            if snippet in text:
                failures.append(
                    f"{path.relative_to(PROJECT_ROOT)} contains forbidden snippet {snippet!r}"
                )

        for line_number, line in enumerate(text.splitlines(), start=1):
            for char in line:
                codepoint = ord(char)
                if 0xE000 <= codepoint <= 0xF8FF:
                    failures.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line_number} "
                        f"contains private-use character U+{codepoint:04X}"
                    )

    assert not failures, "Encoding hygiene violations found:\n" + "\n".join(failures)
