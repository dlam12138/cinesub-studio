from __future__ import annotations

import time
from pathlib import Path

import asr_model_api
from asr_model_locator import (
    locate_asr_model,
    model_target_dir,
    validate_model_directory,
)


def _write_model(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "model.bin").write_bytes(b"model")


def test_locator_supports_flat_snapshot_absolute_and_unicode_paths(tmp_path: Path) -> None:
    model_dir = tmp_path / "模型 目录"
    cache_dir = tmp_path / "缓存" / "hub"

    flat = model_target_dir("small", model_dir)
    _write_model(flat)
    small = locate_asr_model("small", model_dir, cache_dir)
    assert small.available is True
    assert small.source == "models_dir"
    assert Path(small.local_path) == flat.resolve()

    snapshot = (
        cache_dir
        / "models--Systran--faster-whisper-medium"
        / "snapshots"
        / "revision-one"
    )
    _write_model(snapshot)
    medium = locate_asr_model("medium", model_dir, cache_dir)
    assert medium.available is True
    assert medium.source == "huggingface_cache"
    assert Path(medium.local_path) == snapshot.resolve()

    absolute = tmp_path / "自定义 模型"
    _write_model(absolute)
    custom = locate_asr_model(str(absolute), model_dir, cache_dir)
    assert custom.available is True
    assert custom.source == "absolute_path"


def test_incomplete_model_is_not_available(tmp_path: Path) -> None:
    target = model_target_dir("small", tmp_path)
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    valid, missing = validate_model_directory(target)
    assert valid is False
    assert missing == ("model.bin",)
    assert locate_asr_model("small", tmp_path).available is False


def test_download_requires_confirmation_and_publishes_atomically(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")
    monkeypatch.setattr(asr_model_api, "TMP_DIR", tmp_path / "tmp")
    with asr_model_api.DOWNLOAD_LOCK:
        asr_model_api.DOWNLOAD_TASK.update(
            {
                "id": "",
                "status": "idle",
                "model": "",
                "source": "",
                "stage": "",
                "progress": None,
                "started_at": 0,
                "finished_at": 0,
                "error": "",
            }
        )

    try:
        asr_model_api.start_model_download(
            model_name="small",
            source="official",
            confirmed=False,
        )
    except ValueError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("download must require confirmation")
    assert not (tmp_path / "models").exists()

    import huggingface_hub

    def fake_snapshot_download(*, local_dir: str, **_kwargs) -> str:
        _write_model(Path(local_dir))
        return local_dir

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    payload, status = asr_model_api.start_model_download(
        model_name="small",
        source="official",
        confirmed=True,
    )
    assert status == 202
    deadline = time.time() + 3
    while time.time() < deadline:
        task = asr_model_api.get_download_task()["task"]
        if task["status"] != "downloading":
            break
        time.sleep(0.01)
    assert task["status"] == "completed"
    target = model_target_dir("small", tmp_path / "models")
    assert validate_model_directory(target)[0] is True
    assert not (tmp_path / "tmp" / "asr-download" / payload["task"]["id"]).exists()


def test_download_rejects_unknown_model_and_source() -> None:
    for model, source in (("unknown", "official"), ("small", "other")):
        try:
            asr_model_api.start_model_download(
                model_name=model,
                source=source,
                confirmed=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unknown model/source must be rejected")


def test_failed_download_cleans_staging_and_preserves_diagnostic_error(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")
    monkeypatch.setattr(asr_model_api, "TMP_DIR", tmp_path / "tmp")
    with asr_model_api.DOWNLOAD_LOCK:
        asr_model_api.DOWNLOAD_TASK.update(
            {"id": "", "status": "idle", "model": "", "source": "", "stage": ""}
        )

    import huggingface_hub

    def failing_download(*, local_dir: str, **_kwargs) -> str:
        path = Path(local_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / "partial.bin").write_bytes(b"partial")
        raise RuntimeError("mock network failure with token=secret")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", failing_download)
    payload, status = asr_model_api.start_model_download(
        model_name="medium",
        source="mirror",
        confirmed=True,
    )
    assert status == 202
    deadline = time.time() + 3
    while time.time() < deadline:
        task = asr_model_api.get_download_task()["task"]
        if task["status"] != "downloading":
            break
        time.sleep(0.01)

    assert task["status"] == "failed"
    assert "mock network failure" in task["error"]
    assert "secret" not in task["error"]
    assert not (
        tmp_path / "tmp" / "asr-download" / payload["task"]["id"]
    ).exists()
    assert not model_target_dir("medium", tmp_path / "models").exists()


def test_download_is_singleton(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")
    with asr_model_api.DOWNLOAD_LOCK:
        asr_model_api.DOWNLOAD_TASK.update({"id": "", "status": "idle"})

    class DeferredThread:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(asr_model_api.threading, "Thread", DeferredThread)
    _, status = asr_model_api.start_model_download(
        model_name="small", source="official", confirmed=True
    )
    assert status == 202
    try:
        asr_model_api.start_model_download(
            model_name="medium", source="official", confirmed=True
        )
    except asr_model_api.AsrModelDownloadConflict:
        pass
    else:
        raise AssertionError("a second concurrent download must be rejected")
    finally:
        with asr_model_api.DOWNLOAD_LOCK:
            asr_model_api.DOWNLOAD_TASK.update({"id": "", "status": "idle"})
