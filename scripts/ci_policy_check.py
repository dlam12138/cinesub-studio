from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {".venv", ".cache", "models", "uploads", "output", "work", "logs", "tools/python", "tools/cuda", "tools/ffmpeg"}
SECRET_PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb'(?i)["\']api[_-]?key["\']\s*[:=]\s*["\'][^"\']{12,}["\']'),
)


def tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    errors: list[str] = []
    for relative in tracked_files():
        normalized = relative.replace("\\", "/")
        if any(normalized == part or normalized.startswith(part + "/") for part in FORBIDDEN_PARTS):
            errors.append(f"tracked runtime artifact: {relative}")
        path = ROOT / relative
        scan_secrets = not (
            normalized.startswith("tests/")
            or normalized.endswith(".example")
            or normalized == "scripts/ci_policy_check.py"
        )
        if path.is_file() and path.stat().st_size <= 2_000_000:
            data = path.read_bytes()
            if scan_secrets and any(pattern.search(data) for pattern in SECRET_PATTERNS):
                errors.append(f"possible secret: {relative}")
    if errors:
        raise SystemExit("\n".join(errors))
    print("CI policy scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
