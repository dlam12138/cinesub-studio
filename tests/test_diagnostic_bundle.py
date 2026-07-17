from __future__ import annotations

import json
import zipfile
from pathlib import Path

import diagnostic_bundle
import provider_store
import runtime_api
from runtime_paths import RuntimePaths


def test_diagnostic_bundle_is_redacted_and_contains_only_safe_entries(tmp_path: Path, monkeypatch) -> None:
    paths = RuntimePaths("source", tmp_path, tmp_path, tmp_path / "src", tmp_path / "runtime")
    (tmp_path / "logs").mkdir()
    (tmp_path / "work" / "states").mkdir(parents=True)
    (tmp_path / "logs" / "pipeline.log").write_text(
        r"authorization=Bearer-secret C:\Users\tester\movie.srt", encoding="utf-8"
    )
    (tmp_path / "work" / "states" / "movie.state.json").write_text(
        json.dumps({"file": r"C:\media\movie.mkv", "status": "failed", "stage": "translating", "error": "api_key=raw-secret"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_api, "get_runtime_diagnostics", lambda: {"python": r"C:\Users\tester\python.exe"})
    monkeypatch.setattr(
        provider_store,
        "list_providers",
        lambda mask_secret=True: [{"id": "p", "api_key_masked": "abc***xyz", "translation_model": "m"}],
    )
    result = diagnostic_bundle.create_diagnostic_bundle(paths)
    target = tmp_path / "reports" / "diagnostics" / result["file"]
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        combined = b"\n".join(archive.read(name) for name in names)
    assert result["restricted"] is True
    assert "runtime_diagnostics.json" in names
    assert b"raw-secret" not in combined
    assert b"Bearer-secret" not in combined
    assert b"C:\\Users" not in combined
    assert b"movie.srt" not in combined
