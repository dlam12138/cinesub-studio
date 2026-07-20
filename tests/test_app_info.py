from __future__ import annotations

from pathlib import Path

from app_info import APP_VERSION, get_app_info
from runtime_paths import RuntimePaths


def _paths(tmp_path: Path, layout: str) -> RuntimePaths:
    return RuntimePaths(
        layout=layout,
        project_root=tmp_path / "data",
        app_root=tmp_path / "app",
        src_root=tmp_path / "app" / "src",
        runtime_root=tmp_path / "runtime",
    )


def test_source_app_info_is_explicitly_development(monkeypatch, tmp_path):
    monkeypatch.delenv("CINESUB_APP_VERSION", raising=False)
    monkeypatch.delenv("CINESUB_BUILD_FLAVOR", raising=False)

    info = get_app_info(_paths(tmp_path, "source"))

    assert info == {
        "ok": True,
        "version": APP_VERSION,
        "build_flavor": "development",
        "runtime_layout": "source",
        "packaged": False,
        "cuda_runtime_bundled": False,
    }


def test_packaged_unified_info_uses_explicit_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CINESUB_APP_VERSION", "0.6.2")
    monkeypatch.setenv("CINESUB_BUILD_FLAVOR", "unified")

    info = get_app_info(_paths(tmp_path, "packaged"))

    assert info["version"] == "0.6.2"
    assert info["build_flavor"] == "unified"
    assert info["packaged"] is True
    assert info["cuda_runtime_bundled"] is True
    assert set(info) == {
        "ok",
        "version",
        "build_flavor",
        "runtime_layout",
        "packaged",
        "cuda_runtime_bundled",
    }


def test_invalid_flavor_falls_back_without_probing_hardware(monkeypatch, tmp_path):
    monkeypatch.setenv("CINESUB_BUILD_FLAVOR", "cuda-auto-detected-secret-path")

    assert get_app_info(_paths(tmp_path, "source"))["build_flavor"] == "development"
    assert get_app_info(_paths(tmp_path, "packaged"))["build_flavor"] == "unified"


def test_web_server_exposes_read_only_app_info_route():
    import inspect

    import web_server

    source = inspect.getsource(web_server.Handler._do_GET_impl)
    assert 'parsed.path == "/api/app-info"' in source
    assert "get_app_info(PATHS)" in source
