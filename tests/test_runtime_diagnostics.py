import json

import provider_store
import runtime_api
import runtime_env


class VersionInfo(tuple):
    @property
    def major(self):
        return self[0]

    @property
    def minor(self):
        return self[1]


def _patch_runtime(monkeypatch, tmp_path, *, python_minor=13):
    project_root = tmp_path
    cuda_dir = project_root / "tools" / "cuda"
    cuda_dir.mkdir(parents=True)
    (cuda_dir / "cublas64_12.dll").write_text("fake", encoding="utf-8")
    (cuda_dir / "cudnn64_9.dll").write_text("fake", encoding="utf-8")

    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(runtime_env, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(runtime_env, "TOOLS_DIR", project_root / "tools")
    monkeypatch.setattr(runtime_env, "CUDA_DIR", cuda_dir)
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
    monkeypatch.setattr(runtime_env.sys, "base_prefix", str(project_root / f"Python3{python_minor}"))
    monkeypatch.setattr(
        runtime_env.sys,
        "version_info",
        VersionInfo((3, python_minor, 3)),
    )
    monkeypatch.setattr(runtime_env.sys, "version", f"3.{python_minor}.3 test")
    monkeypatch.setattr(
        runtime_env,
        "find_ffmpeg_info",
        lambda root: {
            "ok": True,
            "path": str(project_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"),
            "source": "bundled",
            "source_label": "项目内置",
        },
    )
    monkeypatch.setattr(runtime_env, "_module_status", lambda name: (True, ""))
    monkeypatch.setattr(
        runtime_env,
        "_nvidia_driver_info",
        lambda: {"ok": True, "message": "NVIDIA test driver"},
    )
    monkeypatch.setattr(runtime_env, "_known_models", lambda: [])


def _item(payload, item_id):
    return next(item for item in payload["diagnostic_items"] if item["id"] == item_id)


def test_runtime_diagnostics_preserves_old_fields_and_adds_user_readable_items(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path, python_minor=13)

    payload = runtime_env.runtime_diagnostics()

    assert payload["python_supported"] is False
    assert payload["ffmpeg_ok"] is True
    assert payload["cuda_ready"] is True
    assert payload["ffmpeg_source"] == "bundled"
    assert payload["diagnostic_summary"]["status"] == "warning"
    assert isinstance(payload["diagnostic_items"], list)
    assert set(payload["diagnostic_summary"]) >= {"status", "title", "message"}
    assert all("status" in item for item in payload["diagnostic_items"])
    assert all("blocking" in item for item in payload["diagnostic_items"])

    python_item = _item(payload, "python")
    assert python_item["status"] == "warning"
    assert python_item["blocking"] is False

    assert _item(payload, "output_dir")["status"] == "ok"
    assert _item(payload, "work_dir")["status"] == "ok"
    assert _item(payload, "logs_dir")["status"] == "ok"


def test_runtime_diagnostics_missing_model_cache_is_not_error(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path, python_minor=12)

    payload = runtime_env.runtime_diagnostics()

    model_item = _item(payload, "model_cache")
    assert model_item["status"] == "not_configured"
    assert model_item["blocking"] is False


def test_runtime_directory_diagnostics_probe_existing_dirs_and_delete_temp_files(
    monkeypatch,
    tmp_path,
):
    _patch_runtime(monkeypatch, tmp_path, python_minor=12)
    for path in (runtime_env.OUTPUT_DIR, runtime_env.WORK_DIR, runtime_env.LOGS_DIR):
        path.mkdir(parents=True)

    payload = runtime_env.runtime_diagnostics()

    assert _item(payload, "output_dir")["status"] == "ok"
    assert _item(payload, "work_dir")["status"] == "ok"
    assert _item(payload, "logs_dir")["status"] == "ok"
    for path in (runtime_env.OUTPUT_DIR, runtime_env.WORK_DIR, runtime_env.LOGS_DIR):
        assert list(path.glob(".diagnostic_write_test.*.tmp")) == []


def test_runtime_directory_diagnostics_report_non_directory_as_error(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path, python_minor=12)
    runtime_env.LOGS_DIR.write_text("not a directory", encoding="utf-8")

    payload = runtime_env.runtime_diagnostics()

    logs_item = _item(payload, "logs_dir")
    assert logs_item["status"] == "error"
    assert logs_item["blocking"] is False


def test_runtime_directory_diagnostics_report_not_writable(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path, python_minor=12)
    runtime_env.WORK_DIR.mkdir()

    original_can_write = runtime_env._can_write_in

    def fake_can_write(path):
        if path == runtime_env.WORK_DIR:
            return False
        return original_can_write(path)

    monkeypatch.setattr(runtime_env, "_can_write_in", fake_can_write)

    payload = runtime_env.runtime_diagnostics()

    work_item = _item(payload, "work_dir")
    assert work_item["status"] == "error"
    assert work_item["blocking"] is True


def test_runtime_api_provider_not_configured_is_not_global_error(monkeypatch):
    monkeypatch.setattr(
        runtime_api,
        "runtime_diagnostics",
        lambda: {
            "ok": True,
            "python_supported": True,
            "ffmpeg_ok": True,
            "cuda_ready": True,
            "diagnostic_items": [],
            "diagnostic_summary": {"status": "ok", "title": "环境可用", "message": "当前运行环境检查通过。"},
        },
    )
    monkeypatch.setattr(provider_store, "get_active_provider", lambda: None)

    payload = runtime_api.get_runtime_diagnostics()

    provider_item = _item(payload, "provider")
    assert provider_item["status"] == "not_configured"
    assert provider_item["blocking"] is False
    assert "Web UI 启动" in provider_item["explanation"]
    assert "翻译任务会失败" in provider_item["explanation"]
    assert payload["diagnostic_summary"]["status"] == "not_configured"


def test_runtime_api_provider_secret_is_not_leaked(monkeypatch):
    secret = "sk-real-secret-key"
    monkeypatch.setattr(
        runtime_api,
        "runtime_diagnostics",
        lambda: {
            "ok": True,
            "python_supported": True,
            "ffmpeg_ok": True,
            "cuda_ready": True,
            "diagnostic_items": [],
            "diagnostic_summary": {"status": "ok", "title": "环境可用", "message": "当前运行环境检查通过。"},
        },
    )
    monkeypatch.setattr(
        provider_store,
        "get_active_provider",
        lambda: {
            "id": "deepseek-main",
            "name": "DeepSeek",
            "translation_model": "deepseek-v4-flash",
            "api_key": secret,
        },
    )
    monkeypatch.setattr(provider_store, "mask_api_key", lambda key: "sk-...-masked")

    payload = runtime_api.get_runtime_diagnostics()
    payload_text = json.dumps(payload, ensure_ascii=False)

    assert secret not in payload_text
    provider_item = _item(payload, "provider")
    assert provider_item["status"] == "ok"
    assert "sk-...-masked" in provider_item["value"]
