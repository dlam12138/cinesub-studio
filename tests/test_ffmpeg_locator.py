import sys
from pathlib import Path

import ffmpeg_locator


def _exe_name() -> str:
    return "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


def test_find_ffmpeg_uses_explicit_env_path(monkeypatch, tmp_path):
    ffmpeg_path = tmp_path / _exe_name()
    ffmpeg_path.write_text("fake ffmpeg", encoding="utf-8")

    monkeypatch.setenv("CINESUB_FFMPEG", str(ffmpeg_path))
    monkeypatch.delenv("FFMPEG_PATH", raising=False)

    assert Path(ffmpeg_locator.find_ffmpeg(tmp_path)) == ffmpeg_path


def test_find_ffmpeg_info_uses_explicit_env_directory(monkeypatch, tmp_path):
    ffmpeg_path = tmp_path / _exe_name()
    ffmpeg_path.write_text("fake ffmpeg", encoding="utf-8")

    monkeypatch.setenv("CINESUB_FFMPEG", str(tmp_path))
    monkeypatch.delenv("FFMPEG_PATH", raising=False)

    info = ffmpeg_locator.find_ffmpeg_info(tmp_path)

    assert info["ok"] is True
    assert Path(info["path"]) == ffmpeg_path
    assert info["source"] == "env"
    assert info["source_label"] == "环境变量"


def test_find_ffmpeg_info_ignores_invalid_env_path(monkeypatch, tmp_path):
    bundled = tmp_path / "tools" / "ffmpeg" / "bin" / _exe_name()
    bundled.parent.mkdir(parents=True)
    bundled.write_text("fake bundled ffmpeg", encoding="utf-8")

    monkeypatch.setenv("CINESUB_FFMPEG", str(tmp_path / "not-ffmpeg.exe"))
    monkeypatch.delenv("FFMPEG_PATH", raising=False)

    info = ffmpeg_locator.find_ffmpeg_info(tmp_path)

    assert info["ok"] is True
    assert Path(info["path"]) == bundled.resolve()
    assert info["source"] == "bundled"


def test_find_ffmpeg_prefers_project_local_before_path(monkeypatch, tmp_path):
    bundled = tmp_path / "tools" / "ffmpeg" / "bin" / _exe_name()
    bundled.parent.mkdir(parents=True)
    bundled.write_text("fake bundled ffmpeg", encoding="utf-8")
    system_ffmpeg = tmp_path / "system" / _exe_name()
    system_ffmpeg.parent.mkdir(parents=True)
    system_ffmpeg.write_text("fake system ffmpeg", encoding="utf-8")

    monkeypatch.delenv("CINESUB_FFMPEG", raising=False)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(ffmpeg_locator.shutil, "which", lambda name: str(system_ffmpeg))

    assert Path(ffmpeg_locator.find_ffmpeg(tmp_path)) == bundled.resolve()
    info = ffmpeg_locator.find_ffmpeg_info(tmp_path)
    assert info["source"] == "bundled"
    assert info["source_label"] == "项目内置"


def test_find_ffmpeg_falls_back_to_path(monkeypatch, tmp_path):
    system_ffmpeg = tmp_path / "system" / _exe_name()
    system_ffmpeg.parent.mkdir(parents=True)
    system_ffmpeg.write_text("fake system ffmpeg", encoding="utf-8")

    monkeypatch.delenv("CINESUB_FFMPEG", raising=False)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(ffmpeg_locator.shutil, "which", lambda name: str(system_ffmpeg))

    assert ffmpeg_locator.find_ffmpeg(tmp_path) == str(system_ffmpeg)
    info = ffmpeg_locator.find_ffmpeg_info(tmp_path)
    assert info["ok"] is True
    assert info["source"] == "path"
    assert info["source_label"] == "系统 PATH"


def test_find_ffmpeg_info_reports_not_found(monkeypatch, tmp_path):
    monkeypatch.delenv("CINESUB_FFMPEG", raising=False)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(ffmpeg_locator.shutil, "which", lambda name: None)

    info = ffmpeg_locator.find_ffmpeg_info(tmp_path)

    assert info == {
        "ok": False,
        "path": "",
        "source": "not_found",
        "source_label": "未找到",
    }
