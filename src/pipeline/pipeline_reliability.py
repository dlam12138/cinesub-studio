from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from output_paths import plan_pipeline_outputs


TASK_STEM_LIMIT = 72
LARGE_ARTIFACT_BYTES = 64 * 1024 * 1024
FINGERPRINT_CHUNK_BYTES = 1024 * 1024
SIGNATURE_SCHEMA = 1


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_relative_input_path(path: Path, input_root: Path) -> str:
    relative = path.resolve().relative_to(input_root.resolve()).as_posix()
    return unicodedata.normalize("NFC", relative).casefold()


def sanitize_stem(stem: str) -> str:
    value = unicodedata.normalize("NFC", str(stem or "")).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value)
    value = re.sub(r"\s+", "-", value).strip(" .-")
    value = value[:TASK_STEM_LIMIT].rstrip(" .-")
    return value or "media"


def task_identity(path: Path, input_root: Path) -> tuple[str, str]:
    relative = normalize_relative_input_path(path, input_root)
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
    return f"{sanitize_stem(path.stem)}-{digest}", relative


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    size = stat.st_size
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if size <= FINGERPRINT_CHUNK_BYTES * 2:
            digest.update(handle.read())
            method = "full"
        else:
            digest.update(handle.read(FINGERPRINT_CHUNK_BYTES))
            handle.seek(-FINGERPRINT_CHUNK_BYTES, os.SEEK_END)
            digest.update(handle.read(FINGERPRINT_CHUNK_BYTES))
            method = "head-tail-1m"
    return {
        "schema_version": 1,
        "size": size,
        "mtime_ns": stat.st_mtime_ns,
        "method": method,
        "sha256": digest.hexdigest(),
    }


def artifact_fingerprint(
    path: Path, cached: dict[str, Any] | None = None, *, force_full: bool = False
) -> dict[str, Any] | None:
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size <= 0:
            return None
    except OSError:
        return None
    cached = cached or {}
    resolved = str(path.resolve())
    if (
        cached.get("path") == resolved
        and cached.get("size") == stat.st_size
        and cached.get("mtime_ns") == stat.st_mtime_ns
        and cached.get("sha256")
    ):
        return dict(cached)
    # Large generated artifacts are hashed once at creation and then reused by metadata.
    # A metadata change is the only reason a read-only scan hashes them again.
    return {
        "path": resolved,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
        "hash_kind": "full",
        "large": stat.st_size >= LARGE_ARTIFACT_BYTES and not force_full,
    }


def artifact_matches(path: Path, cached: dict[str, Any] | None) -> bool:
    current = artifact_fingerprint(path, cached)
    return bool(current and cached and current.get("sha256") == cached.get("sha256"))


def artifact_set_fingerprint(
    paths: Iterable[Path], cached: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    cached_by_path = {
        str(row.get("path") or ""): row
        for row in ((cached or {}).get("artifacts") or [])
        if isinstance(row, dict)
    }
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        resolved = str(Path(path).resolve())
        value = artifact_fingerprint(Path(path), cached_by_path.get(resolved))
        if value is None:
            return None
        artifacts.append(value)
    payload = [
        {key: row.get(key) for key in ("path", "size", "mtime_ns", "sha256")}
        for row in artifacts
    ]
    return {"schema_version": 1, "artifacts": artifacts, "sha256": canonical_hash(payload)}


def artifact_set_matches(cached: dict[str, Any] | None) -> bool:
    if not cached or not cached.get("artifacts"):
        return False
    paths = [Path(str(row.get("path") or "")) for row in cached["artifacts"]]
    current = artifact_set_fingerprint(paths, cached)
    return bool(current and current.get("sha256") == cached.get("sha256"))


def stage_signature(stage: str, config_payload: dict[str, Any], upstream: Any) -> str:
    return canonical_hash({
        "schema_version": SIGNATURE_SCHEMA,
        "stage": stage,
        "config": config_payload,
        "upstream": upstream,
    })


@dataclass
class PipelineBlocker:
    code: str
    message: str
    task_id: str = ""
    recovery_action: str = ""


@dataclass
class PipelineTaskPlan:
    task_id: str
    display_name: str
    relative_input_path: str
    input_path: str
    output_stem: str
    category: str
    rebuild_from: str = ""
    state_path: str = ""
    legacy_state_path: str = ""
    planned_migration: bool = False
    input_fingerprint: dict[str, Any] = field(default_factory=dict)
    expected_signatures: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelinePlan:
    tasks: list[PipelineTaskPlan] = field(default_factory=list)
    blockers: list[PipelineBlocker] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    plan_fingerprint: str = ""
    effective_config_hash: str = ""

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self, *, include_private_paths: bool = False) -> dict[str, Any]:
        tasks = []
        for item in self.tasks:
            row = asdict(item)
            if not include_private_paths:
                row.pop("input_path", None)
                row.pop("state_path", None)
                row.pop("legacy_state_path", None)
                row.pop("input_fingerprint", None)
                row.pop("expected_signatures", None)
            tasks.append(row)
        return {
            "ok": self.ok,
            "tasks": tasks,
            "blockers": [asdict(item) for item in self.blockers],
            "counts": dict(self.counts),
            "plan_fingerprint": self.plan_fingerprint,
            "effective_config_hash": self.effective_config_hash,
        }


def _state_data(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _config_payloads(config: Any) -> dict[str, dict[str, Any]]:
    profile = getattr(config, "lang_profile_config", None) or {}
    translation = {
        "provider_id": getattr(config, "provider_id", ""),
        "provider": getattr(config, "api_provider", ""),
        "api_base_hash": canonical_hash(str(getattr(config, "api_base", "") or "")),
        "model": getattr(config, "llm_model", ""),
        "quality_model": getattr(config, "translation_quality_model", ""),
        "target_language": getattr(config, "target_language", ""),
        "prompt_hash": canonical_hash(str(getattr(config, "translation_prompt", "") or "")),
        "profile_id": getattr(config, "language_profile_id", ""),
        "profile_hash": canonical_hash(profile),
        "mode": getattr(config, "translation_mode", ""),
        "strategy": getattr(config, "translation_strategy_mode", ""),
        "reliability": getattr(config, "translation_reliability_mode", ""),
    }
    quality = {
        "profile_quality": profile.get("quality_thresholds", profile.get("quality", {})),
        "target_language": getattr(config, "target_language", ""),
        "translation_mode": getattr(config, "translation_mode", ""),
    }
    return {
        "audio": {"recipe": "pcm-s16le-16000-mono-v1"},
        "asr": getattr(config, "asr_signature_payload", lambda: {})(),
        "translation": translation,
        "quality": quality,
        "final_output": {
            "translate": bool(getattr(config, "translate", True)),
            "formats": list(getattr(config, "subtitle_formats", ["srt"])),
            "translation_mode": getattr(config, "translation_mode", ""),
        },
    }


def expected_stage_signatures(config: Any, state: dict[str, Any] | None, fp: dict[str, Any]) -> dict[str, str]:
    state = state or {}
    artifacts = state.get("artifact_fingerprints", {}) or {}
    payloads = _config_payloads(config)
    input_stage = stage_signature("input", {"recipe": "input-fingerprint-v1"}, fp)
    audio_upstream = fp.get("sha256", "")
    audio = stage_signature("audio", payloads["audio"], audio_upstream)
    audio_sha = (artifacts.get("audio") or {}).get("sha256", "")
    asr = stage_signature("asr", payloads["asr"], audio_sha)
    source_sha = (artifacts.get("source_srt") or {}).get("sha256", "")
    translation = stage_signature("translation", payloads["translation"], source_sha)
    translated_sha = (artifacts.get("translation_output") or {}).get("sha256", "")
    quality = stage_signature("quality", payloads["quality"], [source_sha, translated_sha])
    quality_sha = (artifacts.get("quality_report") or {}).get("sha256", "")
    final_output = stage_signature(
        "final_output", payloads["final_output"], [source_sha, translated_sha, quality_sha]
    )
    return {
        "input": input_stage,
        "audio": audio,
        "asr": asr,
        "translation": translation,
        "quality": quality,
        "final_output": final_output,
    }


def local_provider_preflight(config: Any) -> list[PipelineBlocker]:
    if not bool(getattr(config, "translate", True)):
        return []
    blockers: list[PipelineBlocker] = []
    profile = getattr(config, "lang_profile_config", None) or {}
    profile_provider = str(profile.get("provider_id") or "").strip()
    provider_id = str(getattr(config, "provider_id", "") or "").strip()
    if profile_provider and provider_id and profile_provider != provider_id:
        blockers.append(PipelineBlocker(
            "profile_provider_mismatch",
            "Language Profile 引用的 Provider 与当前选择不一致。",
        ))
    api_base = str(getattr(config, "api_base", "") or "").strip()
    if api_base:
        parsed = urlparse(api_base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            blockers.append(PipelineBlocker("invalid_api_base", "翻译接口地址格式无效。"))
    if not str(getattr(config, "llm_model", "") or "").strip():
        blockers.append(PipelineBlocker("missing_translation_model", "翻译已启用但未配置模型。"))
    if not str(getattr(config, "api_key", "") or os.environ.get("SUBTITLE_LLM_API_KEY", "")).strip():
        blockers.append(PipelineBlocker("missing_api_key", "翻译已启用但未配置 API Key。"))
    return blockers


def build_pipeline_plan(
    config: Any,
    *,
    state_dir: Path,
    video_extensions: Iterable[str],
    read_only: bool = True,
) -> PipelinePlan:
    del read_only  # The implementation is always read-only by contract.
    input_root = Path(config.input_dir)
    output_root = Path(config.output_dir)
    states = {path.name: (_state_data(path), path) for path in state_dir.glob("*.state.json")} if state_dir.exists() else {}
    historical_output_owners: dict[str, list[tuple[str, Path]]] = {}
    for state_filename, (raw_state, state_path) in states.items():
        if not raw_state:
            continue
        owner = str(raw_state.get("task_id") or state_filename.removesuffix(".state.json"))
        output_stem = str(raw_state.get("output_stem") or Path(str(raw_state.get("file") or owner)).stem)
        historical_output_owners.setdefault(output_stem.casefold(), []).append((owner, state_path))
    videos: list[Path] = []
    if input_root.exists():
        extensions = {value.lower() for value in video_extensions}
        videos = sorted(
            path for path in input_root.rglob("*")
            if path.is_file() and path.suffix.lower() in extensions
        )
    identities = [
        (
            path,
            task_identity(path, input_root)[0],
            unicodedata.normalize(
                "NFC", path.resolve().relative_to(input_root.resolve()).as_posix()
            ),
        )
        for path in videos
    ]
    stem_counts: dict[str, int] = {}
    for path, _, _ in identities:
        stem_counts[path.stem.casefold()] = stem_counts.get(path.stem.casefold(), 0) + 1

    plan = PipelinePlan()
    plan.blockers.extend(local_provider_preflight(config))
    claimed_outputs: dict[str, str] = {}
    for path, task_id, relative in identities:
        fp = input_fingerprint(path)
        state_name = f"{task_id}.state.json"
        state_entry = states.get(state_name)
        state = state_entry[0] if state_entry else None
        legacy_name = f"{path.stem}.state.json"
        legacy_entry = states.get(legacy_name)
        planned_migration = False
        legacy_path = ""
        if state is None and legacy_entry:
            if stem_counts[path.stem.casefold()] != 1:
                plan.blockers.append(PipelineBlocker(
                    "ambiguous_legacy_state",
                    f"存在多个同名输入，旧状态 {legacy_name} 无法安全绑定。",
                    task_id,
                ))
            else:
                candidate = legacy_entry[0] or {}
                old_input = str(candidate.get("input_path") or "")
                old_fp = candidate.get("input_fingerprint") or {}
                path_matches = False
                if old_input:
                    try:
                        path_matches = Path(old_input).resolve() == path.resolve()
                    except OSError:
                        path_matches = False
                fingerprint_matches = bool(
                    old_fp.get("sha256")
                    and old_fp.get("sha256") == fp.get("sha256")
                    and old_fp.get("size") == fp.get("size")
                )
                if not path_matches and not fingerprint_matches:
                    plan.blockers.append(PipelineBlocker(
                        "legacy_state_input_unverified",
                        "旧状态记录无法通过输入路径或指纹验证，拒绝自动迁移。",
                        task_id,
                    ))
                else:
                    state = candidate
                    planned_migration = True
                    legacy_path = str(legacy_entry[1])
        duplicate = stem_counts[path.stem.casefold()] > 1
        output_stem = str((state or {}).get("output_stem") or (task_id if duplicate else path.stem))
        associated_state_paths = {
            candidate.resolve()
            for candidate in (
                Path(state_entry[1]) if state_entry else None,
                Path(legacy_path) if legacy_path else None,
            )
            if candidate is not None
        }
        historical_conflict = any(
            owner_path.resolve() not in associated_state_paths
            for _owner, owner_path in historical_output_owners.get(output_stem.casefold(), [])
        )
        if historical_conflict:
            output_stem = task_id
        owner = claimed_outputs.setdefault(output_stem.casefold(), task_id)
        if owner != task_id:
            output_stem = task_id
            claimed_outputs[output_stem.casefold()] = task_id

        outputs = plan_pipeline_outputs(
            output_root=output_root,
            stem=output_stem,
            model=config.model,
            target_language=config.target_language,
            translation_mode=config.translation_mode,
        )
        planned_outputs = {
            outputs.source_srt,
            outputs.translated_srt,
            outputs.bilingual_srt,
            outputs.quality_report,
            outputs.review_needed,
        }
        owned_paths: set[Path] = set()
        if state:
            for key in (
                "source_srt", "translated_srt", "bilingual_srt", "quality_report",
                "semantic_review_report", "asr_review_report",
            ):
                raw_path = str(state.get(key) or "")
                if raw_path:
                    try:
                        owned_paths.add(Path(raw_path).resolve())
                    except OSError:
                        pass
            quality_path = str(state.get("quality_report") or "")
            if quality_path:
                report = Path(quality_path)
                owned_paths.add(
                    report.with_name(report.name.replace(".quality_report.json", ".review_needed.srt")).resolve()
                )
            for metadata in (state.get("artifact_fingerprints", {}) or {}).values():
                raw_path = str((metadata or {}).get("path") or "")
                if raw_path:
                    try:
                        owned_paths.add(Path(raw_path).resolve())
                    except OSError:
                        pass
        for candidate in sorted(planned_outputs):
            if candidate.exists() and candidate.resolve() not in owned_paths:
                plan.blockers.append(PipelineBlocker(
                    "unowned_output_collision",
                    f"输出 {candidate.name} 已存在但没有可验证的任务归属。",
                    task_id,
                ))

        if state:
            state = dict(state)
            hydrated = dict(state.get("artifact_fingerprints", {}) or {})
            raw_artifacts = {
                "audio": state.get("audio_path"),
                "source_srt": state.get("source_srt"),
                "translation_output": state.get("bilingual_srt") or state.get("translated_srt"),
                "quality_report": state.get("quality_report"),
            }
            for key, raw_path in raw_artifacts.items():
                if key not in hydrated and raw_path:
                    value = artifact_fingerprint(Path(str(raw_path)))
                    if value:
                        hydrated[key] = value
            state["artifact_fingerprints"] = hydrated
        expected = expected_stage_signatures(config, state, fp)
        category = "new"
        rebuild_from = ""
        if state:
            signatures = state.get("stage_build_signatures", {}) or {}
            artifacts = state.get("artifact_fingerprints", {}) or {}
            stages = ["input", "audio", "asr"] + (["translation", "quality"] if config.translate else []) + ["final_output"]
            first_invalid = ""
            artifact_keys = {
                "input": "input",
                "audio": "audio",
                "asr": "source_srt",
                "translation": "translation_output",
                "quality": "quality_report",
                "final_output": "final_output",
            }
            for stage in stages:
                cached = artifacts.get(artifact_keys[stage]) or {}
                if stage == "input":
                    stored_input = state.get("input_fingerprint") or {}
                    valid_artifact = bool(
                        stored_input.get("sha256") == fp.get("sha256")
                        and stored_input.get("size") == fp.get("size")
                    )
                elif stage == "final_output":
                    valid_artifact = artifact_set_matches(cached)
                else:
                    artifact_path = Path(str(cached.get("path") or ""))
                    valid_artifact = bool(cached and artifact_matches(artifact_path, cached))
                valid_signature = signatures.get(stage) == expected.get(stage)
                if stage == "input" and not signatures.get("input"):
                    valid_signature = valid_artifact
                if stage == "audio" and not signatures.get("audio"):
                    valid_signature = valid_artifact
                if stage == "asr" and not signatures.get("asr"):
                    valid_signature = (
                        state.get("asr_config_signature") == getattr(config, "asr_signature", lambda: "")()
                        and valid_artifact
                    )
                if not valid_signature or not valid_artifact:
                    first_invalid = stage
                    break
            final_paths_valid = bool(state.get("status") == "completed")
            if not first_invalid and final_paths_valid:
                category = "skip"
            elif first_invalid:
                category = "rebuild"
                rebuild_from = first_invalid
            else:
                category = "reuse"

        item = PipelineTaskPlan(
            task_id=task_id,
            display_name=path.name,
            relative_input_path=relative,
            input_path=str(path.resolve()),
            output_stem=output_stem,
            category=category,
            rebuild_from=rebuild_from,
            state_path=str(state_dir / state_name),
            legacy_state_path=legacy_path,
            planned_migration=planned_migration,
            input_fingerprint=fp,
            expected_signatures=expected,
        )
        plan.tasks.append(item)

    plan.counts = {name: sum(item.category == name for item in plan.tasks) for name in ("new", "reuse", "rebuild", "skip")}
    effective = _config_payloads(config)
    plan.effective_config_hash = canonical_hash(effective)
    plan.plan_fingerprint = canonical_hash({
        "config": plan.effective_config_hash,
        "tasks": [
            {
                "task_id": item.task_id,
                "relative": item.relative_input_path,
                "input": item.input_fingerprint,
                "category": item.category,
                "output_stem": item.output_stem,
            }
            for item in plan.tasks
        ],
        "blockers": [asdict(item) for item in plan.blockers],
    })
    return plan


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_run_record(path: Path) -> dict[str, Any]:
    return _state_data(path) or {}


def write_run_record(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    value.setdefault("schema_version", 1)
    value["updated_at"] = time.time()
    atomic_write_json(path, value)
    return value


class PipelineRunLock:
    def __init__(self, path: Path, *, offset: int = 0):
        self.path = Path(path)
        self.offset = int(offset)
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        required_size = self.offset + 1
        if handle.tell() < required_size:
            handle.write(b"0" * (required_size - handle.tell()))
            handle.flush()
        handle.seek(self.offset)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                os.set_handle_inheritable(msvcrt.get_osfhandle(handle.fileno()), True)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self._handle = handle
        return True

    @property
    def inheritable_handle(self) -> int:
        if self._handle is None or os.name != "nt":
            return -1
        import msvcrt
        return int(msvcrt.get_osfhandle(self._handle.fileno()))

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(self.offset)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("pipeline lock is already held")
        return self

    def __exit__(self, *_args):
        self.release()


def windows_process_creation_filetime(pid: int) -> int:
    if os.name != "nt" or not pid:
        return 0
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return 0
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        return (
            (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        ) if ok else 0
    finally:
        kernel32.CloseHandle(handle)


def process_identity_matches(pid: int, creation_filetime: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        observed = windows_process_creation_filetime(pid)
        return bool(observed and creation_filetime and observed == creation_filetime)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
