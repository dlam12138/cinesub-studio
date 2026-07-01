from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

UTF8 = "utf-8"
UTF8_SIG = "utf-8-sig"

_CONFLICTING_RUN_KWARGS = {"text", "encoding", "errors", "universal_newlines"}


def read_text(path: str | Path, *, user_input: bool = False) -> str:
    """Read project text as UTF-8, accepting a BOM for user-supplied files."""
    encoding = UTF8_SIG if user_input else UTF8
    return Path(path).read_text(encoding=encoding)


def write_text(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding=UTF8)


def read_json(path: str | Path, *, user_input: bool = False) -> Any:
    return json.loads(read_text(path, user_input=user_input))


def write_json(path: str | Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def run_text(
    args: subprocess._CMD,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a command whose stdout/stderr are human-readable text."""
    conflicts = _CONFLICTING_RUN_KWARGS.intersection(kwargs)
    if conflicts:
        names = ", ".join(sorted(conflicts))
        raise TypeError(f"run_text controls these subprocess kwargs: {names}")
    return subprocess.run(
        args,
        text=True,
        encoding=UTF8,
        errors="replace",
        **kwargs,
    )
