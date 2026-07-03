from pathlib import Path

import process_env
from runtime_paths import RuntimePaths


def test_build_child_process_env_sets_project_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("PYTHONPATH", "outside")
    monkeypatch.setattr(
        process_env,
        "add_project_cuda_to_env",
        lambda env: env.__setitem__("CUDA_INJECTED", "1") or True,
    )

    env = process_env.build_child_process_env(tmp_path)

    assert env["HF_HOME"] == str(tmp_path / ".cache" / "huggingface")
    assert env["HF_HUB_CACHE"] == str(tmp_path / ".cache" / "huggingface" / "hub")
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["CUDA_INJECTED"] == "1"
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    for subdir in process_env.SRC_SUBDIRS:
        assert str(tmp_path / "src" / subdir) in env["PYTHONPATH"]


def test_build_child_process_env_uses_resolved_app_and_src_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(process_env, "add_project_cuda_to_env", lambda env: False)
    paths = RuntimePaths(
        layout="release",
        project_root=tmp_path,
        app_root=tmp_path / "app",
        src_root=tmp_path / "app" / "src",
        runtime_root=tmp_path / "runtime",
    )

    env = process_env.build_child_process_env(tmp_path, paths)

    assert env["HF_HOME"] == str(tmp_path / ".cache" / "huggingface")
    assert str(tmp_path / "app") in env["PYTHONPATH"]
    for subdir in process_env.SRC_SUBDIRS:
        assert str(tmp_path / "app" / "src" / subdir) in env["PYTHONPATH"]


def test_redact_project_path_handles_windows_and_forward_slashes():
    project_root = Path(r"D:\CineSub Project")
    text = (
        r"D:\CineSub Project\output\movie.srt "
        "D:/CineSub Project/work/audio.wav outside/path"
    )

    redacted = process_env.redact_project_path(text, project_root)

    assert r"D:\CineSub Project" not in redacted
    assert "D:/CineSub Project" not in redacted
    assert r".\output\movie.srt" in redacted
    assert "./work/audio.wav" in redacted
    assert "outside/path" in redacted
