from __future__ import annotations

from pathlib import Path

import pytest
import venv_config

ROOT = Path(__file__).resolve().parents[1]


def _make_base(tmp_path: Path) -> Path:
    base = tmp_path / "中文 Python" / "python.exe"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"python")
    return base


def _make_venv(tmp_path: Path, base: Path, *, executable: str | None = None) -> Path:
    venv = tmp_path / "中文项目" / ".venv"
    venv.mkdir(parents=True)
    configured = executable or str(base)
    (venv / "pyvenv.cfg").write_text(
        "\n".join(
            [
                f"home = {Path(configured).parent}",
                "include-system-site-packages = false",
                "version = 3.12.10",
                f"executable = {configured}",
                f"command = {configured} -m venv {venv}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return venv


def test_inspect_accepts_utf8_chinese_paths(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    venv = _make_venv(tmp_path, base)

    result = venv_config.inspect_venv_config(venv, expected_base_python=base)

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["executable"] == str(base)


def test_inspect_reports_missing_config(tmp_path: Path) -> None:
    result = venv_config.inspect_venv_config(tmp_path / ".venv")

    assert result["ok"] is False
    assert result["exists"] is False
    assert "pyvenv.cfg is missing" in result["issues"]


def test_inspect_reports_mismatched_or_missing_targets(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    wrong = tmp_path / "椤圭洰" / "python.exe"
    venv = _make_venv(tmp_path, base, executable=str(wrong))

    result = venv_config.inspect_venv_config(venv, expected_base_python=base)

    assert result["ok"] is False
    assert "configured base executable does not exist" in result["issues"]
    assert "configured base executable does not match the active base interpreter" in result["issues"]


def test_repair_is_atomic_and_idempotent(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    venv = _make_venv(tmp_path, base, executable=str(tmp_path / "broken" / "python.exe"))

    first = venv_config.repair_venv_config(venv, base_python=base)
    first_text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    second = venv_config.repair_venv_config(venv, base_python=base)

    assert first["ok"] is True
    assert second["ok"] is True
    assert (venv / "pyvenv.cfg").read_text(encoding="utf-8") == first_text
    assert list(venv.glob(".pyvenv.cfg.*.tmp")) == []


def test_repair_requires_existing_venv_config_and_base(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="virtual environment directory"):
        venv_config.repair_venv_config(tmp_path / ".venv", base_python=tmp_path / "python.exe")

    venv = tmp_path / ".venv"
    venv.mkdir()
    with pytest.raises(ValueError, match="pyvenv.cfg"):
        venv_config.repair_venv_config(venv, base_python=tmp_path / "python.exe")


def test_atomic_replace_failure_preserves_original(tmp_path: Path, monkeypatch) -> None:
    base = _make_base(tmp_path)
    venv = _make_venv(tmp_path, base, executable=str(tmp_path / "broken" / "python.exe"))
    config = venv / "pyvenv.cfg"
    original = config.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(venv_config.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        venv_config.repair_venv_config(venv, base_python=base)

    assert config.read_bytes() == original
    assert list(venv.glob(".pyvenv.cfg.*.tmp")) == []


def test_install_requires_explicit_repair_for_existing_venv() -> None:
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "[switch]$RepairVenvConfig" in text
    assert "$VenvCreated -or $RepairVenvConfig" in text
    assert "venv_config.py" in text
    assert "It was not modified" in text
    assert "install.ps1 -RepairVenvConfig" in text
