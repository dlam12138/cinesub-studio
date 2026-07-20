from pathlib import Path

import runtime_paths


def _make_source_layout(root: Path) -> Path:
    for subdir in runtime_paths.SRC_SUBDIRS:
        (root / "src" / subdir).mkdir(parents=True, exist_ok=True)
    return root / "src" / "tools" / "runtime_paths.py"


def _make_release_layout(root: Path) -> Path:
    for subdir in runtime_paths.SRC_SUBDIRS:
        (root / "app" / "src" / subdir).mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir()
    (root / runtime_paths.RELEASE_MARKER).write_text("portable layout", encoding="utf-8")
    return root / "app" / "src" / "tools" / "runtime_paths.py"


def test_resolve_runtime_paths_detects_source_layout(tmp_path):
    anchor = _make_source_layout(tmp_path)

    paths = runtime_paths.resolve_runtime_paths(anchor)

    assert paths.layout == "source"
    assert paths.project_root == tmp_path.resolve()
    assert paths.app_root == tmp_path.resolve()
    assert paths.src_root == (tmp_path / "src").resolve()
    assert paths.runtime_root == (tmp_path / "runtime").resolve()
    assert paths.python_runtime_dir == (tmp_path / "tools" / "python").resolve()
    assert paths.app_root in paths.pythonpath_entries


def test_resolve_runtime_paths_detects_explicit_release_layout(tmp_path):
    anchor = _make_release_layout(tmp_path)

    paths = runtime_paths.resolve_runtime_paths(anchor)

    assert paths.layout == "release"
    assert paths.project_root == tmp_path.resolve()
    assert paths.app_root == (tmp_path / "app").resolve()
    assert paths.src_root == (tmp_path / "app" / "src").resolve()
    assert paths.runtime_root == (tmp_path / "runtime").resolve()
    assert paths.python_runtime_dir == (tmp_path / "runtime" / "python").resolve()


def test_plain_runtime_directory_does_not_trigger_release_layout(tmp_path):
    anchor = _make_source_layout(tmp_path)
    (tmp_path / "runtime").mkdir()

    paths = runtime_paths.resolve_runtime_paths(anchor)

    assert paths.layout == "source"
    assert paths.project_root == tmp_path.resolve()
    assert paths.app_root == tmp_path.resolve()


def test_release_shape_without_marker_does_not_trigger_release_layout(tmp_path):
    for subdir in runtime_paths.SRC_SUBDIRS:
        (tmp_path / "app" / "src" / subdir).mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime").mkdir()
    anchor = tmp_path / "app" / "src" / "tools" / "runtime_paths.py"

    paths = runtime_paths.resolve_runtime_paths(anchor)

    assert paths.layout == "source"
    assert paths.project_root == (tmp_path / "app").resolve()


def test_resolution_does_not_depend_on_current_working_directory(monkeypatch, tmp_path):
    repo_root = (tmp_path / "repo").resolve()
    anchor = _make_source_layout(repo_root)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    paths = runtime_paths.resolve_runtime_paths(anchor)

    assert paths.project_root == repo_root
    assert paths.src_root == (repo_root / "src").resolve()


def test_resolve_runtime_paths_has_no_directory_creation_side_effects(tmp_path):
    anchor = _make_source_layout(tmp_path)
    for name in ("runtime", "input", "output", "work", "logs"):
        assert not (tmp_path / name).exists()

    paths = runtime_paths.resolve_runtime_paths(anchor)

    assert paths.layout == "source"
    for name in ("runtime", "input", "output", "work", "logs"):
        assert not (tmp_path / name).exists()


def test_resolve_runtime_paths_uses_packaged_code_and_user_roots(monkeypatch, tmp_path):
    packaged_root = (tmp_path / "installed" / "app").resolve()
    user_root = (tmp_path / "local" / "CineSubStudio").resolve()
    monkeypatch.setenv("CINESUB_PACKAGED_ROOT", str(packaged_root))
    monkeypatch.setenv("CINESUB_USER_DATA_ROOT", str(user_root))

    paths = runtime_paths.resolve_runtime_paths(tmp_path / "unrelated.py")

    assert paths.layout == "packaged"
    assert paths.project_root == user_root
    assert paths.app_root == packaged_root / "backend"
    assert paths.src_root == packaged_root / "backend" / "src"
    assert paths.python_runtime_dir == packaged_root / "python"
    assert paths.tools_dir == packaged_root / "tools"
    assert paths.models_dir == user_root / "models"
    assert paths.output_dir == user_root / "output"
    assert paths.logs_dir == user_root / "logs"
    assert paths.cache_dir == user_root / ".cache"
    assert paths.uploads_dir == user_root / "uploads"
