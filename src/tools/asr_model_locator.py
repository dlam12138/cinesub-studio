from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


MODEL_CATALOG: dict[str, dict[str, object]] = {
    "tiny": {
        "repo_id": "Systran/faster-whisper-tiny",
        "estimated_size": "约 75 MB",
        "estimated_bytes": 75 * 1024 * 1024,
    },
    "base": {
        "repo_id": "Systran/faster-whisper-base",
        "estimated_size": "约 145 MB",
        "estimated_bytes": 145 * 1024 * 1024,
    },
    "small": {
        "repo_id": "Systran/faster-whisper-small",
        "estimated_size": "约 500 MB",
        "estimated_bytes": 500 * 1024 * 1024,
    },
    "medium": {
        "repo_id": "Systran/faster-whisper-medium",
        "estimated_size": "约 1.5 GB",
        "estimated_bytes": 1536 * 1024 * 1024,
    },
    "large-v3": {
        "repo_id": "Systran/faster-whisper-large-v3",
        "estimated_size": "约 3 GB",
        "estimated_bytes": 3 * 1024 * 1024 * 1024,
    },
    "distil-large-v3": {
        "repo_id": "Systran/faster-distil-whisper-large-v3",
        "estimated_size": "约 1.5 GB",
        "estimated_bytes": 1536 * 1024 * 1024,
    },
}

MODEL_SOURCES = {
    "official": {
        "label": "Hugging Face 官方",
        "endpoint": "https://huggingface.co",
    },
    "mirror": {
        "label": "hf-mirror",
        "endpoint": "https://hf-mirror.com",
    },
}


@dataclass(frozen=True)
class AsrModelLocation:
    requested: str
    repo_id: str
    available: bool
    source: str
    local_path: str
    missing_files: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["missing_files"] = list(self.missing_files)
        return payload


def model_repo_id(model_name: str) -> str:
    value = str(model_name or "").strip()
    if value in MODEL_CATALOG:
        return str(MODEL_CATALOG[value]["repo_id"])
    if "/" in value and value.count("/") == 1:
        return value
    return f"Systran/faster-whisper-{value}"


def model_cache_name(repo_id: str) -> str:
    return f"models--{repo_id.replace('/', '--')}"


def model_target_dir(model_name: str, model_dir: Path) -> Path:
    return model_dir / model_cache_name(model_repo_id(model_name))


def validate_model_directory(path: Path) -> tuple[bool, tuple[str, ...]]:
    missing: list[str] = []
    for name in ("config.json", "model.bin"):
        candidate = path / name
        try:
            valid = candidate.is_file() and candidate.stat().st_size > 0
        except OSError:
            valid = False
        if not valid:
            missing.append(name)
    return not missing, tuple(missing)


def locate_asr_model(
    model_name: str,
    model_dir: Path,
    hf_cache_dir: Path | None = None,
) -> AsrModelLocation:
    requested_text = str(model_name or "").strip()
    requested_path = Path(requested_text).expanduser()
    repo_id = model_repo_id(requested_text)

    if requested_text and (requested_path.is_absolute() or requested_path.is_dir()):
        valid, missing = validate_model_directory(requested_path)
        return AsrModelLocation(
            requested=requested_text,
            repo_id=repo_id,
            available=valid,
            source="absolute_path",
            local_path=str(requested_path.resolve()) if valid else "",
            missing_files=missing,
        )

    flat = model_target_dir(requested_text, model_dir)
    valid, missing = validate_model_directory(flat)
    if valid:
        return AsrModelLocation(
            requested=requested_text,
            repo_id=repo_id,
            available=True,
            source="models_dir",
            local_path=str(flat.resolve()),
        )

    for snapshot in _snapshot_candidates(repo_id, hf_cache_dir):
        snapshot_valid, snapshot_missing = validate_model_directory(snapshot)
        if snapshot_valid:
            return AsrModelLocation(
                requested=requested_text,
                repo_id=repo_id,
                available=True,
                source="huggingface_cache",
                local_path=str(snapshot.resolve()),
            )
        missing = snapshot_missing

    return AsrModelLocation(
        requested=requested_text,
        repo_id=repo_id,
        available=False,
        source="missing",
        local_path="",
        missing_files=missing,
    )


def installed_models(model_dir: Path, hf_cache_dir: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for model_name in MODEL_CATALOG:
        location = locate_asr_model(model_name, model_dir, hf_cache_dir)
        if location.available:
            rows.append(location.to_dict())
            seen.add(location.local_path.casefold())

    for root, source in _inventory_roots(model_dir, hf_cache_dir):
        for candidate in root:
            valid, _missing = validate_model_directory(candidate)
            if not valid:
                continue
            resolved = str(candidate.resolve())
            if resolved.casefold() in seen:
                continue
            repo_id = _repo_from_cache_name(candidate.parent.name if source == "huggingface_cache" else candidate.name)
            rows.append(
                AsrModelLocation(
                    requested=repo_id,
                    repo_id=repo_id,
                    available=True,
                    source=source,
                    local_path=resolved,
                ).to_dict()
            )
            seen.add(resolved.casefold())
    return rows


def model_plan(
    model_name: str,
    source: str,
    *,
    model_dir: Path,
    hf_cache_dir: Path | None = None,
) -> dict:
    if model_name not in MODEL_CATALOG:
        raise ValueError("Unsupported ASR model.")
    if source not in MODEL_SOURCES:
        raise ValueError("Unsupported ASR model source.")
    location = locate_asr_model(model_name, model_dir, hf_cache_dir)
    catalog = MODEL_CATALOG[model_name]
    selected_source = MODEL_SOURCES[source]
    return {
        "id": model_name,
        "repo_id": catalog["repo_id"],
        "available": location.available,
        "local_path": location.local_path,
        "local_source": location.source,
        "estimated_size": catalog["estimated_size"],
        "estimated_bytes": catalog["estimated_bytes"],
        "source": source,
        "source_label": selected_source["label"],
        "source_url": selected_source["endpoint"],
        "target_dir": str(model_target_dir(model_name, model_dir).resolve()),
        "download_required": not location.available,
        "download_supported": True,
    }


def _snapshot_candidates(repo_id: str, hf_cache_dir: Path | None) -> Iterable[Path]:
    if hf_cache_dir is None:
        return ()
    repo_root = hf_cache_dir / model_cache_name(repo_id) / "snapshots"
    if not repo_root.is_dir():
        return ()
    try:
        return tuple(path for path in sorted(repo_root.iterdir(), reverse=True) if path.is_dir())
    except OSError:
        return ()


def _inventory_roots(
    model_dir: Path,
    hf_cache_dir: Path | None,
) -> list[tuple[tuple[Path, ...], str]]:
    flat: tuple[Path, ...] = ()
    if model_dir.is_dir():
        try:
            flat = tuple(path for path in model_dir.glob("models--*--*") if path.is_dir())
        except OSError:
            pass
    snapshots: list[Path] = []
    if hf_cache_dir and hf_cache_dir.is_dir():
        try:
            for repo_root in hf_cache_dir.glob("models--*--*"):
                snap_root = repo_root / "snapshots"
                if snap_root.is_dir():
                    snapshots.extend(path for path in snap_root.iterdir() if path.is_dir())
        except OSError:
            pass
    return [(flat, "models_dir"), (tuple(snapshots), "huggingface_cache")]


def _repo_from_cache_name(value: str) -> str:
    text = value.removeprefix("models--")
    return text.replace("--", "/", 1)
