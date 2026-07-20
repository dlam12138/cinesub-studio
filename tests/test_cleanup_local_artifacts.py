from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path


def _load_cleanup():
    script = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_local_artifacts.py"
    spec = importlib.util.spec_from_file_location("cleanup_local_artifacts", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for dirname in ("src", "acceptance", "config", ".tmp", "dist", "logs", "output"):
        (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("print('tracked source')\n", encoding="utf-8")
    (root / "src" / "__pycache__").mkdir(parents=True)
    (root / "src" / "__pycache__" / "app.pyc").write_bytes(b"cache")
    (root / ".tmp" / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    (root / "dist" / "cinesub-portable").mkdir(parents=True)
    (root / "dist" / "cinesub-portable" / "payload.txt").write_text("payload\n", encoding="utf-8")
    (root / "dist" / "cinesub-portable-m6.7-rc1.zip").write_bytes(b"zip")
    (root / "logs" / "pipeline.log").write_text("log\n", encoding="utf-8")
    old_time = time.time() - 30 * 24 * 60 * 60
    os.utime(root / "logs" / "pipeline.log", (old_time, old_time))
    (root / "output" / "movie.srt").write_text("subtitle\n", encoding="utf-8")
    (root / "project_evaluation_report.md").write_text("do not touch\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "config" / "providers.local.json").write_text('{"api_key":"secret"}\n', encoding="utf-8")
    return root.resolve()


def test_default_dry_run_deletes_nothing(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    result = cleanup.main(["--repo-root", str(repo), "--category", "tmp"])

    assert result == 0
    assert (repo / ".tmp" / "scratch.txt").is_file()


def test_apply_is_required_for_deletion(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")
    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"tmp"},
        tracked_files=set(),
        tracked_ok=True,
    )

    assert [candidate.relative for candidate in plan.candidates] == [".tmp"]
    assert (repo / ".tmp").exists()


def test_tracked_files_are_never_selected(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"tmp", "dist"},
        tracked_files={".tmp/scratch.txt", "dist/cinesub-portable/payload.txt"},
        tracked_ok=True,
    )

    assert ".tmp" not in {candidate.relative for candidate in plan.candidates}
    assert "dist/cinesub-portable" not in {candidate.relative for candidate in plan.candidates}


def test_project_evaluation_report_is_never_selected(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        paths=["project_evaluation_report.md"],
        tracked_files=set(),
        tracked_ok=True,
    )

    assert plan.candidates == []
    assert any("project_evaluation_report.md: refusing protected path" in item for item in plan.skipped)


def test_repo_external_paths_are_refused(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        paths=[".."],
        tracked_files=set(),
        tracked_ok=True,
    )

    assert plan.candidates == []
    assert any("outside project root" in item for item in plan.skipped)


def test_path_cannot_target_protected_roots(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    for protected in ("src", "acceptance", "config", "README.md"):
        plan = cleanup.build_cleanup_plan(
            repo_root=repo,
            paths=[protected],
            tracked_files=set(),
            tracked_ok=True,
        )
        assert plan.candidates == []
        assert any("refusing protected path" in item for item in plan.skipped)


def test_git_detection_failure_disables_apply(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")
    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"tmp"},
        tracked_files=set(),
        tracked_ok=False,
        tracked_warning="git ls-files failed",
    )

    code, messages = cleanup.apply_cleanup_plan(plan)

    assert code == 2
    assert "tracked-file detection failed" in messages[0]
    assert (repo / ".tmp" / "scratch.txt").is_file()


def test_outputs_require_confirm_user_data(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")
    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"outputs"},
        tracked_files=set(),
        tracked_ok=True,
    )

    assert "output" in {candidate.relative for candidate in plan.candidates}
    code, messages = cleanup.apply_cleanup_plan(plan, confirm_user_data=False)

    assert code == 2
    assert "--confirm-user-data" in messages[0]
    assert (repo / "output" / "movie.srt").is_file()


def test_release_zip_requires_include_release_archives(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    default_plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"dist"},
        tracked_files=set(),
        tracked_ok=True,
    )
    include_plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"dist"},
        include_release_archives=True,
        tracked_files=set(),
        tracked_ok=True,
    )

    assert "dist/cinesub-portable-m6.7-rc1.zip" not in {
        candidate.relative for candidate in default_plan.candidates
    }
    assert "dist/cinesub-portable-m6.7-rc1.zip" in {
        candidate.relative for candidate in include_plan.candidates
    }


def test_logs_category_does_not_select_logs_directory(tmp_path):
    cleanup = _load_cleanup()
    repo = _make_repo(tmp_path / "repo")

    plan = cleanup.build_cleanup_plan(
        repo_root=repo,
        categories={"logs"},
        tracked_files=set(),
        tracked_ok=True,
        older_than_days=14,
    )

    assert "logs" not in {candidate.relative for candidate in plan.candidates}
    assert "logs/pipeline.log" in {candidate.relative for candidate in plan.candidates}
