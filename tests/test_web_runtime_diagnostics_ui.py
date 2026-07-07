from __future__ import annotations

import json
from pathlib import Path

import runtime_api
import runtime_env


HTML_PATH = Path(__file__).parent.parent / "web" / "index.html"


class VersionInfo(tuple):
    @property
    def major(self):
        return self[0]

    @property
    def minor(self):
        return self[1]


def _read_index_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _patch_runtime(monkeypatch, tmp_path, *, ffmpeg_ok=True, version_ok=True):
    project_root = tmp_path
    ffmpeg_path = project_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if ffmpeg_ok:
        ffmpeg_path.parent.mkdir(parents=True)
        ffmpeg_path.write_text("fake", encoding="utf-8")

    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(runtime_env, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(runtime_env, "APP_ROOT", project_root)
    monkeypatch.setattr(runtime_env, "SRC_ROOT", project_root / "src")
    monkeypatch.setattr(runtime_env, "RUNTIME_ROOT", project_root / "runtime")
    monkeypatch.setattr(runtime_env, "TOOLS_DIR", project_root / "tools")
    monkeypatch.setattr(runtime_env, "CUDA_DIR", project_root / "tools" / "cuda")
    monkeypatch.setattr(runtime_env, "PYTHON_DIR", project_root / "tools" / "python")
    monkeypatch.setattr(runtime_env, "WHEELHOUSE_DIR", project_root / "tools" / "wheelhouse")
    monkeypatch.setattr(runtime_env, "MODEL_DIR", project_root / "models")
    monkeypatch.setattr(runtime_env, "CACHE_DIR", project_root / ".cache")
    monkeypatch.setattr(runtime_env, "TMP_DIR", project_root / ".tmp")
    monkeypatch.setattr(runtime_env, "OUTPUT_DIR", project_root / "output")
    monkeypatch.setattr(runtime_env, "WORK_DIR", project_root / "work")
    monkeypatch.setattr(runtime_env, "LOGS_DIR", project_root / "logs")
    monkeypatch.setattr(runtime_env.sys, "executable", str(venv_python))
    monkeypatch.setattr(runtime_env.sys, "prefix", str(project_root / ".venv"))
    monkeypatch.setattr(runtime_env.sys, "base_prefix", str(project_root / "Python312"))
    monkeypatch.setattr(runtime_env.sys, "version_info", VersionInfo((3, 12, 3)))
    monkeypatch.setattr(runtime_env.sys, "version", "3.12.3 test")
    monkeypatch.setattr(runtime_env, "_module_status", lambda name: (True, ""))
    monkeypatch.setattr(
        runtime_env,
        "_nvidia_driver_info",
        lambda: {"ok": False, "message": "nvidia-smi not found"},
    )
    monkeypatch.setattr(runtime_env, "_known_models", lambda: [])
    monkeypatch.setattr(
        runtime_env,
        "find_ffmpeg_info",
        lambda root: {
            "ok": ffmpeg_ok,
            "path": str(ffmpeg_path) if ffmpeg_ok else "",
            "source": "bundled" if ffmpeg_ok else "not_found",
            "source_label": "项目内置" if ffmpeg_ok else "未找到",
        },
    )

    def fake_run(args, **kwargs):
        assert args[1] == "-version"
        assert kwargs["timeout"] <= 3
        if not version_ok:
            raise OSError("version probe failed")

        class Result:
            returncode = 0
            stdout = "ffmpeg version test-build\nbuilt with test"
            stderr = ""

        return Result()

    monkeypatch.setattr(runtime_env.subprocess, "run", fake_run)


def _item(payload, item_id):
    return next(item for item in payload["diagnostic_items"] if item["id"] == item_id)


def test_runtime_tab_and_manual_refresh_exist():
    html = _read_index_html()
    assert 'data-tab="runtime"' in html
    assert 'id="tab-runtime"' in html
    assert "运行环境" in html
    assert "刷新诊断" in html
    assert "FFmpeg 状态" in html
    assert "模型状态" in html
    assert "运行目录" in html
    assert "设备信息" in html
    assert "Python / 应用运行时" in html


def test_diagnostics_payload_keeps_stable_fields_and_adds_optional_details(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path)

    payload = runtime_env.runtime_diagnostics()

    assert payload["ffmpeg_source"] == "bundled"
    assert set(payload["diagnostic_summary"]) >= {"status", "title", "message"}
    assert isinstance(payload["diagnostic_items"], list)
    assert all("status" in item for item in payload["diagnostic_items"])
    assert all("blocking" in item for item in payload["diagnostic_items"])
    assert payload["ffmpeg_version"].startswith("ffmpeg version")
    assert payload["ffmpeg_env_names"] == ["CINESUB_FFMPEG", "FFMPEG_PATH"]

    ffmpeg = _item(payload, "ffmpeg")
    assert ffmpeg["details"]["path"].endswith("ffmpeg.exe")
    assert ffmpeg["details"]["version"].startswith("ffmpeg version")


def test_ffmpeg_missing_and_version_probe_failure_do_not_raise(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path, ffmpeg_ok=True, version_ok=False)
    payload = runtime_env.runtime_diagnostics()
    ffmpeg = _item(payload, "ffmpeg")
    assert ffmpeg["status"] == "ok"
    assert "version probe failed" in ffmpeg["details"]["version_error"]
    version_item = _item(payload, "ffmpeg_version")
    assert version_item["status"] == "warning"
    assert version_item["blocking"] is False

    _patch_runtime(monkeypatch, tmp_path / "missing", ffmpeg_ok=False)
    payload = runtime_env.runtime_diagnostics()
    ffmpeg = _item(payload, "ffmpeg")
    assert ffmpeg["status"] == "error"
    assert ffmpeg["blocking"] is True
    assert payload["ffmpeg_ok"] is False


def test_model_status_does_not_trigger_downloads(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path)

    payload = runtime_env.runtime_diagnostics()
    model_item = _item(payload, "model_cache")

    assert model_item["status"] == "not_configured"
    assert model_item["details"]["downloads_triggered"] is False
    assert model_item["details"]["known_model_count"] == 0
    assert "download_model_file" not in json.dumps(payload, ensure_ascii=False)


def test_runtime_directory_checks_are_structured_and_do_not_create_missing_dirs(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path)
    missing_dirs = [
        runtime_env.RUNTIME_ROOT,
        runtime_env.OUTPUT_DIR,
        runtime_env.WORK_DIR,
        runtime_env.LOGS_DIR,
        runtime_env.MODEL_DIR,
        runtime_env.TMP_DIR,
        runtime_env.CACHE_DIR / "huggingface",
    ]
    assert all(not path.exists() for path in missing_dirs)

    payload = runtime_env.runtime_diagnostics()

    for item_id in ("runtime_root", "output_dir", "work_dir", "logs_dir", "models_dir", "tmp_dir", "hf_cache_dir"):
        item = _item(payload, item_id)
        assert "details" in item
        assert "exists" in item["details"]
        assert "writable" in item["details"]
    assert all(not path.exists() for path in missing_dirs)


def test_runtime_api_exception_returns_user_facing_payload(monkeypatch):
    monkeypatch.setattr(runtime_api, "runtime_diagnostics", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    payload = runtime_api.get_runtime_diagnostics()

    assert payload["ok"] is False
    assert payload["diagnostic_summary"]["status"] == "error"
    item = _item(payload, "runtime_diagnostics")
    assert item["status"] == "error"
    assert item["blocking"] is True


def test_ui_does_not_expose_future_management_features():
    lower = _read_index_html().lower()
    assert "model hub" not in lower
    assert "model store" not in lower
    assert "model installer" not in lower
    assert "model management" not in lower
    assert "模型管理" not in lower
    assert "配音" not in lower
    assert "dubbing" not in lower
    assert "tts" not in lower
    assert "voice clone" not in lower
    assert "lip.sync" not in lower
