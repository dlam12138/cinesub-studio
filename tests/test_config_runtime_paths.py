from __future__ import annotations

from pathlib import Path

import language_profile_store
import provider_store
import runtime_paths


def _make_source_layout(root: Path) -> Path:
    for subdir in runtime_paths.SRC_SUBDIRS:
        (root / "src" / subdir).mkdir(parents=True, exist_ok=True)
    return root / "src" / "config" / "provider_store.py"


def _make_release_layout(root: Path) -> Path:
    for subdir in runtime_paths.SRC_SUBDIRS:
        (root / "app" / "src" / subdir).mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir()
    (root / runtime_paths.RELEASE_MARKER).write_text("portable layout\n", encoding="utf-8")
    return root / "app" / "src" / "config" / "provider_store.py"


def test_provider_config_path_preserves_source_layout(tmp_path):
    anchor = _make_source_layout(tmp_path / "source")

    project_root, config_dir, config_path = provider_store._resolve_provider_config_paths(anchor)

    assert project_root == (tmp_path / "source").resolve()
    assert config_dir == (tmp_path / "source" / "config").resolve()
    assert config_path == (tmp_path / "source" / "config" / "providers.local.json").resolve()


def test_provider_config_path_uses_release_root_config(tmp_path):
    anchor = _make_release_layout(tmp_path / "release")

    project_root, config_dir, config_path = provider_store._resolve_provider_config_paths(anchor)

    assert project_root == (tmp_path / "release").resolve()
    assert config_dir == (tmp_path / "release" / "config").resolve()
    assert config_path == (tmp_path / "release" / "config" / "providers.local.json").resolve()
    assert "app" not in config_path.relative_to((tmp_path / "release").resolve()).parts


def test_language_profile_config_path_preserves_source_layout(tmp_path):
    anchor = _make_source_layout(tmp_path / "source")

    project_root, config_path = language_profile_store._resolve_language_profile_config_path(anchor)

    assert project_root == (tmp_path / "source").resolve()
    assert config_path == (tmp_path / "source" / "config" / "language_profiles.local.json").resolve()


def test_language_profile_config_path_uses_release_root_config(tmp_path):
    anchor = _make_release_layout(tmp_path / "release")

    project_root, config_path = language_profile_store._resolve_language_profile_config_path(anchor)

    assert project_root == (tmp_path / "release").resolve()
    assert config_path == (tmp_path / "release" / "config" / "language_profiles.local.json").resolve()
    assert "app" not in config_path.relative_to((tmp_path / "release").resolve()).parts
