from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

REQUIRED_KEYS = ("home", "executable")


def _normalized(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError:
        resolved = path.expanduser().absolute()
    return os.path.normcase(str(resolved))


def _read_config(path: Path) -> tuple[dict[str, str], list[str], list[str]]:
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, [], ["pyvenv.cfg is missing"]
    except UnicodeDecodeError:
        return {}, [], ["pyvenv.cfg is not valid UTF-8"]
    except OSError as exc:
        return {}, [], [f"pyvenv.cfg could not be read: {exc}"]

    values: dict[str, str] = {}
    lines = text.splitlines()
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    for key in REQUIRED_KEYS:
        if not values.get(key):
            issues.append(f"missing {key} entry")
    return values, lines, issues


def inspect_venv_config(
    venv: Path | str,
    *,
    expected_base_python: Path | str | None = None,
) -> dict[str, Any]:
    venv_path = Path(venv).expanduser().resolve(strict=False)
    config_path = venv_path / "pyvenv.cfg"
    values, _lines, issues = _read_config(config_path)

    home_text = values.get("home", "")
    executable_text = values.get("executable", "")
    home_path = Path(home_text) if home_text else None
    executable_path = Path(executable_text) if executable_text else None
    if home_path is not None and not home_path.is_dir():
        issues.append("configured home directory does not exist")
    if executable_path is not None and not executable_path.is_file():
        issues.append("configured base executable does not exist")

    expected_text = ""
    if expected_base_python:
        expected_path = Path(expected_base_python).expanduser().resolve(strict=False)
        expected_text = str(expected_path)
        if not expected_path.is_file():
            issues.append("expected base executable does not exist")
        elif executable_path is not None and _normalized(executable_path) != _normalized(expected_path):
            issues.append("configured base executable does not match the active base interpreter")
        if home_path is not None and _normalized(home_path) != _normalized(expected_path.parent):
            issues.append("configured home does not match the active base interpreter")

    return {
        "ok": not issues,
        "venv": str(venv_path),
        "config_path": str(config_path),
        "exists": config_path.is_file(),
        "home": home_text,
        "executable": executable_text,
        "expected_base_executable": expected_text,
        "issues": issues,
    }


def _replace_config_lines(
    lines: list[str],
    *,
    base_python: Path,
    venv: Path,
) -> str:
    replacements = {
        "home": str(base_python.parent),
        "executable": str(base_python),
        "command": f"{base_python} -m venv {venv}",
    }
    output: list[str] = []
    replaced: set[str] = set()
    for line in lines:
        key, sep, _value = line.partition("=")
        normalized_key = key.strip().lower()
        if sep and normalized_key in replacements:
            output.append(f"{normalized_key} = {replacements[normalized_key]}")
            replaced.add(normalized_key)
        else:
            output.append(line)
    for key in ("home", "executable", "command"):
        if key not in replaced:
            output.append(f"{key} = {replacements[key]}")
    return "\n".join(output).rstrip() + "\n"


def repair_venv_config(
    venv: Path | str,
    *,
    base_python: Path | str,
) -> dict[str, Any]:
    venv_path = Path(venv).expanduser().resolve(strict=False)
    config_path = venv_path / "pyvenv.cfg"
    base_path = Path(base_python).expanduser().resolve(strict=False)
    if not venv_path.is_dir():
        raise ValueError(f"virtual environment directory does not exist: {venv_path}")
    if not config_path.is_file():
        raise ValueError(f"pyvenv.cfg does not exist: {config_path}")
    if not base_path.is_file():
        raise ValueError(f"base Python executable does not exist: {base_path}")

    _values, lines, read_issues = _read_config(config_path)
    if read_issues and any("could not be read" in issue for issue in read_issues):
        raise ValueError("; ".join(read_issues))
    replacement = _replace_config_lines(lines, base_python=base_path, venv=venv_path)

    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".pyvenv.cfg.",
            suffix=".tmp",
            dir=config_path.parent,
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    result = inspect_venv_config(venv_path, expected_base_python=base_path)
    if not result["ok"]:
        raise RuntimeError("repaired pyvenv.cfg did not validate: " + "; ".join(result["issues"]))
    result["repaired"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or explicitly repair a project venv pyvenv.cfg.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "repair"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--venv", default=".venv")
        sub.add_argument("--base-python", default="")
    args = parser.parse_args(argv)

    try:
        if args.command == "repair":
            if not args.base_python:
                raise ValueError("--base-python is required for repair")
            payload = repair_venv_config(args.venv, base_python=args.base_python)
        else:
            payload = inspect_venv_config(
                args.venv,
                expected_base_python=args.base_python or None,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
