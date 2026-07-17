import json
from pathlib import Path

import pytest
from app_info import get_app_info
from versioning import read_version, validate_consumers

ROOT = Path(__file__).resolve().parents[1]


def test_all_version_consumers_match_authoritative_file(monkeypatch):
    monkeypatch.delenv("CINESUB_APP_VERSION", raising=False)
    expected = read_version(ROOT)
    assert validate_consumers(ROOT) == expected
    assert get_app_info()["version"] == expected


def test_version_mismatch_fails_before_build(tmp_path):
    (tmp_path / "desktop").mkdir()
    (tmp_path / "VERSION").write_text("0.6.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.1"\n', encoding="utf-8")
    (tmp_path / "desktop" / "package.json").write_text(json.dumps({"version": "0.6.0"}), encoding="utf-8")
    (tmp_path / "desktop" / "package-lock.json").write_text(
        json.dumps({"version": "0.6.0", "packages": {"": {"version": "0.6.0"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pyproject.toml=0.6.1"):
        validate_consumers(tmp_path)
