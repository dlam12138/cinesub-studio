from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest


def _load_builder():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_portable_release.py"
    spec = importlib.util.spec_from_file_location("build_portable_release", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_repo(root: Path) -> Path:
    for dirname in ("src", "web", "scripts"):
        (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("print('app')\n", encoding="utf-8")
    (root / "src" / "provider_fields.py").write_text(
        "\n".join(
            [
                "api_key = None",
                "access_token = None",
                "refresh_token = None",
                "provider.get('api_key')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (root / "scripts" / "helper.ps1").write_text("Write-Host safe\n", encoding="utf-8")
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_secret_sentinel.py").write_text(
        "SENTINEL = 'sk-test-M5-SECRET-SHOULD-NOT-LEAK'\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (root / "start_app.py").write_text("print('start')\n", encoding="utf-8")
    (root / "start_web.ps1").write_text("Write-Host start\n", encoding="utf-8")
    (root / "install.ps1").write_text("Write-Host install\n", encoding="utf-8")
    (root / "tools" / "python").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "python" / "python.exe").write_text("fake runtime, do not execute\n", encoding="utf-8")
    (root / "tools" / "ffmpeg" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").write_text("fake ffmpeg\n", encoding="utf-8")

    secret_config = root / "config"
    secret_config.mkdir()
    (secret_config / "providers.local.json").write_text('{"api_key":"secret-key"}\n', encoding="utf-8")
    for dirname in (".git", ".venv", ".cache", ".tmp", "models", "uploads", "output", "work", "logs", "archive", "failed"):
        (root / dirname).mkdir(parents=True, exist_ok=True)
        (root / dirname / "artifact.txt").write_text("runtime artifact\n", encoding="utf-8")
    (root / "output" / "movie.zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    (root / "output" / "movie.quality_report.json").write_text("{}\n", encoding="utf-8")
    (root / "output" / "movie.review_needed.srt").write_text("review\n", encoding="utf-8")
    (root / "uploads" / "movie.mp4").write_text("media\n", encoding="utf-8")
    return root.resolve()


def test_builder_creates_portable_layout_with_fake_runtime_without_executing_it(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    result = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    )

    out = result.output_dir
    assert (out / ".portable-layout").is_file()
    assert (out / "app" / "src" / "app.py").is_file()
    assert (out / "runtime" / "python" / "python.exe").read_text(encoding="utf-8").startswith("fake runtime")
    assert (out / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file()
    assert (out / "release_manifest.json").is_file()
    assert (out / "release_report.md").is_file()
    assert (out / "release_checksums.sha256").is_file()
    for dirname in ("config", "input", "output", "work", "logs", "uploads", "models", ".cache"):
        assert (out / dirname).is_dir()
    assert list((out / "config").iterdir()) == []
    assert list((out / "output").iterdir()) == []
    assert list((out / "work").iterdir()) == []
    assert list((out / "logs").iterdir()) == []


def test_builder_uses_whitelist_and_does_not_copy_secrets_or_runtime_artifacts(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    out = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    ).output_dir

    app = out / "app"
    assert not (app / "config" / "providers.local.json").exists()
    for forbidden in (".git", ".venv", ".cache", ".tmp", "models", "uploads", "output", "work", "logs", "archive", "failed", "tools", "tests"):
        assert not (app / forbidden).exists()


def test_builder_manifest_and_report_use_release_relative_paths(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    result = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    report = result.report_path.read_text(encoding="utf-8")

    assert manifest["builder"] == "m6.7-release-candidate-packaging"
    assert manifest["version"] == "m6.7-rc1"
    assert manifest["paths"] == {
        "output_root": ".",
        "app_root": "app",
        "runtime_python_root": "runtime/python",
        "checksums": "release_checksums.sha256",
    }
    assert manifest["copied_file_count"] == result.copied_file_count
    assert manifest["total_bytes"] == result.total_bytes
    assert manifest["largest_files"]
    assert manifest["checksums"]["path"] == "release_checksums.sha256"
    assert manifest["checksums"]["covered_file_count"] > 0
    assert manifest["excluded_summary"]["top_level"]["tests"] == "not included in portable app"
    assert manifest["leak_scan"]["status"] == "passed"
    assert str(repo) not in result.manifest_path.read_text(encoding="utf-8")
    assert str(repo) not in report
    assert "sk-test-M5-SECRET-SHOULD-NOT-LEAK" not in report
    assert "Generated from a local source checkout" in report


def test_builder_does_not_fail_on_source_code_secret_field_names(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    out = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    ).output_dir

    copied_source = (out / "app" / "src" / "provider_fields.py").read_text(encoding="utf-8")
    assert "api_key" in copied_source
    assert "access_token" in copied_source
    assert "refresh_token" in copied_source


def test_builder_rejects_secret_looking_values_in_copied_content(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")
    secret = "sk-" + ("A" * 30)
    (repo / "src" / "bad_secret.py").write_text(f'CONFIG = {{"api_key": "{secret}"}}\n', encoding="utf-8")

    with pytest.raises(builder.BuildError, match="leak scan"):
        builder.build_portable_release(
            repo_root=repo,
            output=repo / "dist" / "cinesub-portable",
            python_runtime=repo / "tools" / "python",
        )


def test_builder_skips_content_scan_inside_copied_python_runtime(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")
    package_dir = repo / "tools" / "python" / "Lib" / "site-packages" / "third_party"
    package_dir.mkdir(parents=True)
    (package_dir / "constants.py").write_text(
        'EXAMPLE = {"access_token": "abcdefghijklmnopqrstuvwxyz"}\n',
        encoding="utf-8",
    )

    out = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    ).output_dir

    assert (out / "runtime" / "python" / "Lib" / "site-packages" / "third_party" / "constants.py").is_file()


def test_builder_checksums_cover_payload_with_release_relative_paths(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    result = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    )

    text = result.checksums_path.read_text(encoding="utf-8")
    assert "release_checksums.sha256" not in text
    assert "release_manifest.json" not in text
    assert "release_report.md" not in text
    assert str(repo) not in text
    assert "app/src/app.py" in text
    assert "runtime/python/python.exe" in text
    for line in text.splitlines():
        digest, relative = line.split("  ", 1)
        assert len(digest) == 64
        assert "\\" not in relative
        assert not Path(relative).is_absolute()


def test_builder_creates_zip_with_single_top_level_dir_and_sidecar_sha256(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    result = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
        version="m6.7-rc1",
        make_zip=True,
    )

    assert result.zip_path == repo / "dist" / "cinesub-portable-m6.7-rc1.zip"
    assert result.zip_sha256_path == repo / "dist" / "cinesub-portable-m6.7-rc1.zip.sha256"
    assert result.zip_path.is_file()
    assert result.zip_sha256_path.is_file()

    expected_digest = hashlib.sha256(result.zip_path.read_bytes()).hexdigest()
    assert result.zip_sha256_path.read_text(encoding="utf-8") == (
        f"{expected_digest}  cinesub-portable-m6.7-rc1.zip\n"
    )

    with ZipFile(result.zip_path) as archive:
        names = archive.namelist()
    assert names
    assert {name.split("/", 1)[0] for name in names} == {"cinesub-portable"}
    assert "cinesub-portable/release_manifest.json" in names
    assert "cinesub-portable/release_report.md" in names
    assert "cinesub-portable/release_checksums.sha256" in names
    forbidden_fragments = (
        "/.git/",
        "/.venv/",
        "/dist/",
        "/tools/python/",
        "/.tmp/",
        "/uploads/movie.mp4",
        "/output/movie.zh.srt",
        "/output/movie.quality_report.json",
        "/output/movie.review_needed.srt",
        "/config/providers.local.json",
    )
    for name in names:
        assert not any(fragment in f"/{name}" for fragment in forbidden_fragments)

    manifest_text = result.manifest_path.read_text(encoding="utf-8")
    report_text = result.report_path.read_text(encoding="utf-8")
    assert result.zip_path.name not in manifest_text
    assert "zip_sha256" not in manifest_text.lower()
    assert result.zip_path.name not in report_text


def test_builder_rejects_runtime_payload_if_it_appears_in_release_root(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")
    output = repo / "dist" / "cinesub-portable"

    original_mkdir = Path.mkdir

    def mkdir_and_inject(self, *args, **kwargs):
        result = original_mkdir(self, *args, **kwargs)
        if self == output / "output":
            (self / "leaked.srt").write_text("leak\n", encoding="utf-8")
        return result

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "mkdir", mkdir_and_inject)
    try:
        with pytest.raises(builder.BuildError, match="rejected file"):
            builder.build_portable_release(
                repo_root=repo,
                output=output,
                python_runtime=repo / "tools" / "python",
            )
    finally:
        monkeypatch.undo()


def test_builder_fails_clearly_when_python_exe_is_missing(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")
    (repo / "tools" / "python" / "python.exe").unlink()

    with pytest.raises(builder.BuildError, match="python.exe"):
        builder.build_portable_release(
            repo_root=repo,
            output=repo / "dist" / "cinesub-portable",
            python_runtime=repo / "tools" / "python",
        )


def test_start_app_bat_uses_safe_quoted_paths_and_propagates_errorlevel(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")

    out = builder.build_portable_release(
        repo_root=repo,
        output=repo / "dist" / "cinesub-portable",
        python_runtime=repo / "tools" / "python",
    ).output_dir
    text = (out / "start_app.bat").read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in text
    assert 'if not exist "runtime\\python\\python.exe"' in text
    assert '"runtime\\python\\python.exe" -B "app\\start_app.py"' in text
    assert "exit /b %ERRORLEVEL%" in text


def test_force_replace_rejects_unsafe_output_paths(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")
    unsafe_outputs = [
        ".",
        "..",
        repo,
        repo.parent,
        repo / "dist",
        repo / "dist" / "nested" / "portable",
        repo / "not-dist" / "portable",
    ]
    anchor = Path(repo.anchor)
    if anchor != repo:
        unsafe_outputs.append(anchor)

    for output in unsafe_outputs:
        with pytest.raises(builder.BuildError):
            builder.build_portable_release(
                repo_root=repo,
                output=output,
                python_runtime=repo / "tools" / "python",
                force=True,
            )


def test_force_replace_allows_direct_child_of_repo_dist(tmp_path):
    builder = _load_builder()
    repo = _make_repo(tmp_path / "repo")
    output = repo / "dist" / "cinesub-portable"
    output.mkdir(parents=True)
    (output / "old.txt").write_text("old\n", encoding="utf-8")

    result = builder.build_portable_release(
        repo_root=repo,
        output=output,
        python_runtime=repo / "tools" / "python",
        force=True,
    )

    assert result.output_dir == output.resolve()
    assert not (output / "old.txt").exists()
