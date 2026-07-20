from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_version(root: Path | None = None) -> str:
    value = ((root or project_root()) / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("VERSION must contain one semantic version.")
    return value


def validate_consumers(root: Path | None = None) -> str:
    import tomllib

    root = root or project_root()
    expected = read_version(root)
    with (root / "pyproject.toml").open("rb") as handle:
        python_version = tomllib.load(handle)["project"]["version"]
    desktop_version = json.loads((root / "desktop" / "package.json").read_text(encoding="utf-8"))["version"]
    lock_data = json.loads((root / "desktop" / "package-lock.json").read_text(encoding="utf-8"))
    lock_version = lock_data["version"]
    lock_root_version = lock_data.get("packages", {}).get("", {}).get("version")
    mismatches = []
    if python_version != expected:
        mismatches.append(f"pyproject.toml={python_version}")
    if desktop_version != expected:
        mismatches.append(f"desktop/package.json={desktop_version}")
    if lock_version != expected or lock_root_version != expected:
        mismatches.append(f"desktop/package-lock.json={lock_version}/{lock_root_version}")
    if mismatches:
        raise ValueError(f"VERSION={expected} does not match: {', '.join(mismatches)}")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("show", "check"), nargs="?", default="check")
    args = parser.parse_args()
    print(read_version() if args.command == "show" else validate_consumers())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
