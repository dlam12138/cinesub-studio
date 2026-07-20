from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest


def _load_builder():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_portable_release.py"
    spec = importlib.util.spec_from_file_location("build_portable_release", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_repo(root: Path) -> tuple[Path, Path]:
    (root / "VERSION").write_text("0.6.2\n", encoding="utf-8")
    for path in (
        root / "tools" / "python" / "python.exe",
        root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
        root / "tools" / "cuda" / "cublas64_12.dll",
        root / "tools" / "cuda" / "cublasLt64_12.dll",
        root / "tools" / "cuda" / "cudnn64_9.dll",
        root / ".venv" / "Scripts" / "python.exe",
        root / "packaging" / "windows" / "THIRD_PARTY_NOTICES.md",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    model = root / "models" / "models--Systran--faster-whisper-small"
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (model / filename).parent.mkdir(parents=True, exist_ok=True)
        (model / filename).write_bytes(b"model")
    unpacked = root / "fake-electron"
    for path in (
        unpacked / "CineSubStudio.exe",
        unpacked / "resources" / "app" / "python" / "python.exe",
        unpacked / "resources" / "app" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        unpacked / "resources" / "app" / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
        unpacked / "resources" / "app" / "tools" / "cuda" / "cublas64_12.dll",
        unpacked / "resources" / "app" / "tools" / "cuda" / "cublasLt64_12.dll",
        unpacked / "resources" / "app" / "tools" / "cuda" / "cudnn64_9.dll",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"electron")
    return root, unpacked


def _disable_external_build_steps(monkeypatch, builder):
    monkeypatch.setattr(builder, "_validate_portable_python", lambda _root: None)
    monkeypatch.setattr(
        builder,
        "_prepare_runtime",
        lambda root: (root / "packaging" / "windows" / "runtime"),
    )


def test_release_name_is_versioned():
    builder = _load_builder()
    assert (
        builder._release_name("0.6.2")
        == "CineSubStudio-0.6.2-windows-x64-portable"
    )


def test_builder_creates_exe_only_portable_zip(monkeypatch, tmp_path):
    builder = _load_builder()
    repo, unpacked = _make_repo(tmp_path)
    _disable_external_build_steps(monkeypatch, builder)

    result = builder.build_portable_release(
        repo_root=repo,
        version="0.6.2",
        electron_unpacked=unpacked,
        github_asset_limit=1024 * 1024,
    )

    assert result.zip_path.is_file()
    assert result.zip_sha256_path.is_file()
    with ZipFile(result.zip_path) as archive:
        names = archive.namelist()
        top = "CineSubStudio-0.6.2-windows-x64-portable/"
        assert all(name.startswith(top) for name in names)
        assert top + "CineSubStudio.exe" in names
        assert top + "data/models/models--Systran--faster-whisper-small/model.bin" in names
        assert not any(name.endswith((".bat", ".ps1")) for name in names)
        assert not any("/tests/" in name or "/research/" in name for name in names)


def test_builder_splits_cuda_when_asset_limit_is_exceeded(monkeypatch, tmp_path):
    builder = _load_builder()
    repo, unpacked = _make_repo(tmp_path)
    _disable_external_build_steps(monkeypatch, builder)
    cuda_file = (
        unpacked
        / "resources"
        / "app"
        / "tools"
        / "cuda"
        / "cublasLt64_12.dll"
    )
    cuda_file.write_bytes(random.Random(0).randbytes(20_000))

    result = builder.build_portable_release(
        repo_root=repo,
        version="0.6.2",
        electron_unpacked=unpacked,
        github_asset_limit=22_000,
    )

    assert result.split_cuda is True
    assert result.cuda_addon_path and result.cuda_addon_path.is_file()
    assert result.cuda_addon_sha256_path and result.cuda_addon_sha256_path.is_file()
    with ZipFile(result.zip_path) as archive:
        assert not any("/resources/app/tools/cuda/" in name for name in archive.namelist())
    with ZipFile(result.cuda_addon_path) as archive:
        assert any(
            name.endswith("/resources/app/tools/cuda/cublasLt64_12.dll")
            for name in archive.namelist()
        )


def test_builder_manifest_records_portable_data_and_model(monkeypatch, tmp_path):
    builder = _load_builder()
    repo, unpacked = _make_repo(tmp_path)
    _disable_external_build_steps(monkeypatch, builder)

    result = builder.build_portable_release(
        repo_root=repo,
        version="0.6.2",
        electron_unpacked=unpacked,
    )
    with ZipFile(result.zip_path) as archive:
        manifest_name = (
            "CineSubStudio-0.6.2-windows-x64-portable/release_manifest.json"
        )
        manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
    assert manifest["layout"] == "electron-portable"
    assert manifest["entrypoint"] == "CineSubStudio.exe"
    assert manifest["data_root"] == "data"
    assert manifest["bundled_models"] == ["small"]
    assert manifest["cuda_bundled"] is True


def test_builder_rejects_incomplete_model(tmp_path):
    builder = _load_builder()
    repo, _unpacked = _make_repo(tmp_path)
    (repo / "models" / "models--Systran--faster-whisper-small" / "model.bin").unlink()
    with pytest.raises(builder.BuildError, match="small model"):
        builder._validate_inputs(repo)


def test_package_scan_rejects_user_config_and_scripts(tmp_path):
    builder = _load_builder()
    root = tmp_path / "portable"
    root.mkdir()
    (root / "providers.local.json").write_text("{}", encoding="utf-8")
    (root / "start_app.bat").write_text("@echo off", encoding="utf-8")
    with pytest.raises(builder.BuildError, match="forbidden"):
        builder._scan_package(root)


def test_sha256_sidecar_matches_zip(monkeypatch, tmp_path):
    builder = _load_builder()
    repo, unpacked = _make_repo(tmp_path)
    _disable_external_build_steps(monkeypatch, builder)
    result = builder.build_portable_release(
        repo_root=repo,
        version="0.6.2",
        electron_unpacked=unpacked,
    )
    digest, filename = result.zip_sha256_path.read_text(encoding="utf-8").split()
    assert digest == builder._sha256(result.zip_path)
    assert filename == result.zip_path.name
