from pathlib import Path

import pipeline_api


def test_pipeline_background_command_uses_sys_executable_and_resolved_src_root(monkeypatch, tmp_path):
    fake_python = str(tmp_path / "runtime" / "python" / "python.exe")
    fake_src_root = tmp_path / "app" / "src"
    monkeypatch.setattr(pipeline_api.sys, "executable", fake_python)
    monkeypatch.setattr(pipeline_api, "SRC_ROOT", fake_src_root)
    monkeypatch.setattr(pipeline_api, "_active_provider_id", lambda: "")
    monkeypatch.setattr(pipeline_api, "_active_language_profile_id", lambda: "")

    command = pipeline_api._build_background_command(
        action="run",
        provider_id="",
        language_profile_id="",
        input_dir="",
        model="small",
        device="auto",
        compute_type="",
        translate_enabled=True,
        language="",
        local_files_only=False,
        subtitle_formats=["srt"],
        ass_style_id="clean-cn",
    )

    assert command[:3] == [
        fake_python,
        "-B",
        str(fake_src_root / "pipeline" / "batch_worker.py"),
    ]
    assert command[0] not in {"python", "python3", "py"}


def test_pipeline_readonly_command_uses_sys_executable_and_resolved_src_root(monkeypatch, tmp_path):
    captured = {}
    fake_python = str(tmp_path / "runtime" / "python" / "python.exe")
    fake_src_root = tmp_path / "app" / "src"
    monkeypatch.setattr(pipeline_api.sys, "executable", fake_python)
    monkeypatch.setattr(pipeline_api, "SRC_ROOT", fake_src_root)
    monkeypatch.setattr(pipeline_api, "_pipeline_env", lambda: {})

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(pipeline_api.subprocess, "run", fake_run)

    payload = pipeline_api.run_pipeline_command("scan")

    assert payload["ok"] is True
    assert captured["command"][:3] == [
        fake_python,
        "-B",
        str(fake_src_root / "pipeline" / "batch_worker.py"),
    ]
    assert captured["kwargs"]["cwd"] == str(pipeline_api.PROJECT_ROOT)
