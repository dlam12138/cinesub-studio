from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import ctranslate2


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
    revision: str = ""
    error: str = ""

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
    if not _has_any_nonempty(path, ("tokenizer.json", "tokenizer.model")):
        missing.append("tokenizer.*")
    if not _has_any_nonempty(
        path,
        ("vocabulary.json", "vocabulary.txt", "vocabulary.bin"),
    ):
        missing.append("vocabulary.*")
    try:
        contains_model = ctranslate2.contains_model(str(path))
    except (OSError, RuntimeError, ValueError):
        contains_model = False
    if not contains_model:
        missing.append("ctranslate2_model")
    return not missing, tuple(dict.fromkeys(missing))


def locate_asr_model(
    model_name: str,
    model_dir: Path,
    hf_cache_dir: Path | None = None,
    *,
    revision: str = "",
) -> AsrModelLocation:
    requested_text = str(model_name or "").strip()
    requested_path = Path(requested_text).expanduser()
    repo_id = model_repo_id(requested_text)

    allowed_roots = tuple(
        root.resolve() for root in (model_dir, hf_cache_dir) if root is not None
    )
    if requested_text and (requested_path.is_absolute() or requested_path.is_dir()):
        resolved_requested = requested_path.resolve()
        if not _inside_any_root(resolved_requested, allowed_roots):
            return AsrModelLocation(
                requested=requested_text,
                repo_id=repo_id,
                available=False,
                source="outside_allowed_model_root",
                local_path="",
                error="model_path_outside_allowed_root",
            )
        valid, missing = validate_model_directory(requested_path)
        return AsrModelLocation(
            requested=requested_text,
            repo_id=repo_id,
            available=valid,
            source="absolute_path",
            local_path=str(resolved_requested) if valid else "",
            missing_files=missing,
            revision=requested_path.name if requested_path.parent.name == "snapshots" else "",
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

    valid_snapshots: list[tuple[Path, str]] = []
    for snapshot, source in _snapshot_candidates(repo_id, model_dir, hf_cache_dir, revision):
        snapshot_valid, snapshot_missing = validate_model_directory(snapshot)
        if snapshot_valid:
            valid_snapshots.append((snapshot, source))
        missing = snapshot_missing
    if len(valid_snapshots) == 1:
        snapshot, source = valid_snapshots[0]
        return AsrModelLocation(
            requested=requested_text,
            repo_id=repo_id,
            available=True,
            source=source,
            local_path=str(snapshot.resolve()),
            revision=snapshot.name,
        )
    if len(valid_snapshots) > 1:
        return AsrModelLocation(
            requested=requested_text,
            repo_id=repo_id,
            available=False,
            source="ambiguous",
            local_path="",
            error="multiple_valid_model_snapshots",
        )

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
            cache_name = (
                candidate.parent.parent.name
                if source in {"models_dir_snapshot", "huggingface_cache"}
                else candidate.name
            )
            repo_id = _repo_from_cache_name(cache_name)
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


def _snapshot_candidates(
    repo_id: str,
    model_dir: Path,
    hf_cache_dir: Path | None,
    revision: str = "",
) -> Iterable[tuple[Path, str]]:
    roots = [(model_dir, "models_dir_snapshot")]
    if hf_cache_dir is not None:
        roots.append((hf_cache_dir, "huggingface_cache"))
    candidates: list[tuple[Path, str]] = []
    for root, source in roots:
        snapshot_root = root / model_cache_name(repo_id) / "snapshots"
        if not snapshot_root.is_dir():
            continue
        try:
            if revision:
                paths = (snapshot_root / revision,)
            else:
                paths = tuple(sorted(snapshot_root.iterdir()))
            candidates.extend((path, source) for path in paths if path.is_dir())
        except OSError:
            continue
    return tuple(candidates)


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
    snapshots: list[tuple[Path, str]] = []
    for root, source in (
        (model_dir, "models_dir_snapshot"),
        (hf_cache_dir, "huggingface_cache"),
    ):
        if root is None or not root.is_dir():
            continue
        try:
            for repo_root in root.glob("models--*--*"):
                snap_root = repo_root / "snapshots"
                if snap_root.is_dir():
                    snapshots.extend(
                        (path, source) for path in snap_root.iterdir() if path.is_dir()
                    )
        except OSError:
            pass
    rows: list[tuple[tuple[Path, ...], str]] = [(flat, "models_dir")]
    for source in ("models_dir_snapshot", "huggingface_cache"):
        rows.append((tuple(path for path, item_source in snapshots if item_source == source), source))
    return rows


def _repo_from_cache_name(value: str) -> str:
    text = value.removeprefix("models--")
    return text.replace("--", "/", 1)


def _has_any_nonempty(path: Path, names: tuple[str, ...]) -> bool:
    for name in names:
        candidate = path / name
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def _inside_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)
