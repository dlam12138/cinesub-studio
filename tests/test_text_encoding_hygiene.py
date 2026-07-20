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

FORBIDDEN_CODEPOINTS = [
    (0xFFFD,),
    # Specific UTF-8-as-GB18030 mojibake examples for common user-facing terms.
    (0x59AF, 0x2033, 0x7037),  # model
    (0x93BA, 0x30E5, 0x5F5B),  # API/interface
    (0x935A, 0xE21C, 0x6564),  # enabled/start
    (0x6D93, 0xE15F, 0x6783),  # Chinese
    (0x74BA, 0xE21A, 0x7DDE),  # path
    (0x93C3, 0x72B3, 0x7876),  # unable
    (0x9239, 0x20AC),
    (0x923A, 0x6128),
    (0x7459, 0xFF46, 0x703D),
]

FORBIDDEN_SNIPPETS = ["".join(chr(codepoint) for codepoint in item) for item in FORBIDDEN_CODEPOINTS]


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


def test_web_index_has_no_milestone4_mojibake_markers() -> None:
    text = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    marker_codepoints = [0x59AF, 0x93BA, 0x935A, 0x6D93, 0x7037, 0xFFFD]

    counts = {f"U+{codepoint:04X}": text.count(chr(codepoint)) for codepoint in marker_codepoints}

    assert all(count == 0 for count in counts.values()), counts
