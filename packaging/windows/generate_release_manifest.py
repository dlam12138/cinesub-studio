from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def build_manifest(*, output_dir: Path, runtime_dir: Path, version: str, flavor: str) -> dict:
    output_dir = output_dir.resolve()
    runtime_dir = runtime_dir.resolve()
    artifacts = []
    current_installer_name = f"CineSubStudio-{version}-windows-x64-setup.exe"
    for path in sorted(output_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.name == "release_manifest.json":
            continue
        if path.is_file() and path.name == current_installer_name:
            artifacts.append({
                "name": path.name,
                "type": "file",
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
        elif path.is_dir() and path.name.endswith("unpacked"):
            artifacts.append({
                "name": path.name,
                "type": "directory",
                "size_bytes": _tree_size(path),
                "sha256": None,
            })

    cuda_root = runtime_dir / "tools" / "cuda"
    ffmpeg_root = runtime_dir / "tools" / "ffmpeg" / "bin"
    python_root = runtime_dir / "python"
    cuda_bundled = (
        (cuda_root / "cublas64_12.dll").is_file()
        and bool(list(cuda_root.glob("cudnn*_9.dll")))
    )
    if flavor != "unified":
        raise RuntimeError(f"Unsupported release flavor: {flavor}")
    if not cuda_bundled:
        raise RuntimeError(
            "Unified release manifest cannot be generated without a complete staged CUDA runtime."
        )
    if not artifacts:
        raise RuntimeError(f"No build artifacts found under: {output_dir}")

    return {
        "schema_version": 1,
        "product": "CineSub Studio",
        "version": version,
        "platform": "windows-x64",
        "build_flavor": flavor,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "components": {
            "portable_python": (python_root / "python.exe").is_file(),
            "ffmpeg": (ffmpeg_root / "ffmpeg.exe").is_file(),
            "ffprobe": (ffmpeg_root / "ffprobe.exe").is_file(),
            "cuda_runtime": cuda_bundled,
            "nvidia_driver": False,
            "whisper_models": False,
        },
        "artifacts": artifacts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a CineSub Windows release manifest.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--flavor", choices=("unified",), required=True)
    args = parser.parse_args()

    manifest = build_manifest(
        output_dir=args.output_dir,
        runtime_dir=args.runtime_dir,
        version=args.version,
        flavor=args.flavor,
    )
    destination = args.output_dir.resolve() / "release_manifest.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Release manifest: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
