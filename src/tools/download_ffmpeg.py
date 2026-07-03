#!/usr/bin/env python3
"""Download FFmpeg into the project-local tools/ffmpeg/bin directory."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from urllib.request import urlopen

from encoding_utils import run_text
from ffmpeg_locator import find_ffmpeg
from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths(Path(__file__).resolve())
PROJECT_ROOT = PATHS.project_root
TMP_DIR = PROJECT_ROOT / ".tmp" / "ffmpeg-download"
ZIP_PATH = TMP_DIR / "ffmpeg-release-essentials.zip"
INSTALL_BIN = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"
FFMPEG_EXE = INSTALL_BIN / "ffmpeg.exe"
FFPROBE_EXE = INSTALL_BIN / "ffprobe.exe"
FFPLAY_EXE = INSTALL_BIN / "ffplay.exe"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _download(url: str, dest: Path, chunk_size: int = 65536) -> None:
    print(f"Downloading: {url}")
    print(f"  -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as response:
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header else None
        downloaded = 0
        with dest.open("wb") as file:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    mb_done = downloaded / 1024 / 1024
                    mb_total = total / 1024 / 1024
                    print(f"\r  {mb_done:.1f} / {mb_total:.1f} MB ({pct:.0f}%)", end="", flush=True)
    print("\n  Download complete.")


def _extract_binaries(zip_path: Path) -> None:
    print(f"Extracting FFmpeg binaries from {zip_path.name} ...")
    wanted = {
        "ffmpeg.exe": FFMPEG_EXE,
        "ffprobe.exe": FFPROBE_EXE,
        "ffplay.exe": FFPLAY_EXE,
    }
    found: set[str] = set()
    INSTALL_BIN.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            name = Path(member.filename).name
            target = wanted.get(name)
            if target is None:
                continue
            with zf.open(member) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            found.add(name)
            print(f"  Extracted: {target}")

    required = {"ffmpeg.exe", "ffprobe.exe"}
    missing = required - found
    if missing:
        raise RuntimeError(f"Missing required binaries in archive: {', '.join(sorted(missing))}")
    if "ffplay.exe" not in found:
        print("  WARN: ffplay.exe not found; it is optional for CineSub Studio.")


def _verify_binary(path: Path) -> str:
    result = run_text(
        [str(path), "-version"],
        capture_output=True,
        check=True,
    )
    return result.stdout.splitlines()[0] if result.stdout else str(path)


def main() -> int:
    existing = find_ffmpeg(PROJECT_ROOT)
    if existing and Path(existing).resolve() == FFMPEG_EXE.resolve() and FFPROBE_EXE.exists():
        print(f"Built-in FFmpeg already available: {existing}")
        return 0
    if existing:
        print(f"FFmpeg already available: {existing}")
        print("Bundled install path remains: tools/ffmpeg/bin/")

    try:
        _download(FFMPEG_URL, ZIP_PATH)
        _extract_binaries(ZIP_PATH)
        ZIP_PATH.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Download failed: {exc}")
        return 1

    try:
        print(f"Verified: {_verify_binary(FFMPEG_EXE)}")
        print(f"Verified: {_verify_binary(FFPROBE_EXE)}")
    except Exception as exc:
        print(f"Verification failed: {exc}")
        return 1

    print()
    print(f"Done. FFmpeg is installed at: {FFMPEG_EXE}")
    print("The script does not modify system PATH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
