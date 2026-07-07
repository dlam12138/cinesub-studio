from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_portable.ps1"
READINESS_DOC = ROOT / "docs" / "windows_portable_release_readiness.md"
QUICKSTART_DOC = ROOT / "docs" / "windows_portable_quickstart.md"
ACCEPTANCE = ROOT / "acceptance" / "m14_windows_portable_release_readiness.md"
GITIGNORE = ROOT / ".gitignore"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_pwsh(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_readiness_and_quickstart_docs_exist_and_document_smoke():
    readiness = _read(READINESS_DOC)
    quickstart = _read(QUICKSTART_DOC)
    combined = readiness + "\n" + quickstart

    assert "Windows Portable Release Readiness" in readiness
    assert ".\\start_web.ps1 -Smoke -NoBrowser -NonInteractive" in combined
    assert "http://127.0.0.1:7860/" in combined
    assert "M14 does not bundle Python" in combined
    assert "not an official release" in combined


def test_packaging_helper_exists_with_required_parameters_and_allowlist():
    text = _read(SCRIPT)

    assert "param(" in text
    assert "$StagingDir" in text
    assert "$Zip" in text
    assert "$DryRun" in text
    assert "git ls-files" in text
    assert "Test-AllowedFile" in text
    assert "start_web.ps1" in text
    assert "start_app.py" in text
    assert 'StartsWith("src/")' in text
    assert 'StartsWith("web/")' in text
    assert 'StartsWith("docs/")' in text


def test_packaging_helper_hard_blocks_local_secrets_and_runtime_artifacts():
    text = _read(SCRIPT)

    blocked = [
        "config/providers.local.json",
        "config/language_profiles.local.json",
        ".env",
        "token",
        "secret",
        "api[_-]?key",
        ".venv",
        "audit",
        "tests",
        "acceptance",
        "models",
        "tools/(python|wheelhouse|cuda|ffmpeg)",
        "project_evaluation_report.md",
    ]
    for marker in blocked:
        assert marker in text


def test_dry_run_does_not_create_staging_or_zip(tmp_path):
    staging = tmp_path / "CineSubStudio-portable"

    result = _run_pwsh([str(SCRIPT), "-DryRun", "-StagingDir", str(staging)])

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Dry-run complete" in output
    assert "Included tracked files" in output
    assert "Excluded tracked files" in output
    assert not staging.exists()
    assert not staging.with_suffix(".zip").exists()


def test_dry_run_reports_include_and_exclude_manifest():
    result = _run_pwsh([str(SCRIPT), "-DryRun", "-StagingDir", "dist\\CineSubStudio-portable"])

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "+ start_web.ps1" in output
    assert "+ start_app.py" in output
    assert "+ src/core/transcribe.py" in output
    assert "- tests/test_local_launcher_readiness.py" in output
    assert "- acceptance/m13_local_launcher_readiness.md" in output


def test_staging_build_uses_allowlist_and_generates_placeholders(tmp_path):
    staging = tmp_path / "CineSubStudio-portable"

    result = _run_pwsh([str(SCRIPT), "-StagingDir", str(staging)])

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert (staging / "start_web.ps1").is_file()
    assert (staging / "start_app.py").is_file()
    assert (staging / "src" / "core" / "transcribe.py").is_file()
    assert (staging / "web" / "index.html").is_file()
    assert (staging / "README_QUICKSTART.md").is_file()
    assert (staging / "tools" / "ffmpeg" / "README_PLACE_FFMPEG_HERE.txt").is_file()
    assert (staging / "models" / "README_PLACE_MODELS_HERE.txt").is_file()

    assert not (staging / "tests").exists()
    assert not (staging / "acceptance").exists()
    assert not (staging / "audit").exists()
    assert not (staging / "config" / "providers.local.json").exists()
    assert not (staging / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").exists()

    ffmpeg_text = _read(staging / "tools" / "ffmpeg" / "README_PLACE_FFMPEG_HERE.txt")
    model_text = _read(staging / "models" / "README_PLACE_MODELS_HERE.txt")
    assert "tools/ffmpeg/bin/ffmpeg.exe" in ffmpeg_text
    assert "M14 does not download models" in model_text


def test_zip_requires_ignored_dist_or_release_path(tmp_path):
    staging = tmp_path / "outside-portable"

    result = _run_pwsh([str(SCRIPT), "-StagingDir", str(staging), "-Zip"])

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "dist/ or release/" in output


def test_gitignore_keeps_m14_artifacts_ignored_and_acceptance_tracked():
    text = _read(GITIGNORE)

    assert "dist/" in text
    assert "release/" in text
    assert "*.portable.zip" in text
    assert "CineSubStudio-portable/" in text
    assert "!acceptance/m14_windows_portable_release_readiness.md" in text


def test_docs_explain_old_builder_is_not_replaced():
    text = _read(READINESS_DOC)

    assert "scripts/build_portable_release.py" in text
    assert "preserved unchanged" in text
    assert "does not replace the older builder" in text


def test_no_electron_tauri_installer_or_code_signing_implementation_added():
    candidate_files = [
        ROOT / "package.json",
        ROOT / "tauri.conf.json",
        ROOT / "src-tauri",
        ROOT / "installer",
    ]
    for path in candidate_files:
        assert not path.exists()

    script_text = _read(SCRIPT).lower()
    forbidden = ["electron", "tauri", "signtool", "auto-update", "auto updater"]
    for marker in forbidden:
        assert marker not in script_text


def test_acceptance_note_exists_and_records_m14_boundaries():
    text = _read(ACCEPTANCE)

    assert "M14 Windows Portable Release Readiness" in text
    assert "c98d896" in text
    assert "not an official release" in text
    assert "No offline standalone validation" in text
    assert "No Electron/Tauri" in text
    assert "No committed release zip" in text
    assert "scripts/build_portable_release.py" in text


def test_placeholders_are_generated_not_committed_to_source_tree():
    source_placeholders = [
        ROOT / "tools" / "ffmpeg" / "README_PLACE_FFMPEG_HERE.txt",
        ROOT / "models" / "README_PLACE_MODELS_HERE.txt",
    ]
    for path in source_placeholders:
        assert not path.exists()


def test_script_does_not_reference_network_or_model_downloads():
    text = _read(SCRIPT).lower()

    forbidden = [
        "invoke-webrequest",
        "start-bitstransfer",
        "download_ffmpeg",
        "download_model",
        "huggingface",
        "snapshot_download",
    ]
    for marker in forbidden:
        assert marker not in text


def test_environment_has_powershell_for_helper_tests():
    assert os.name == "nt"
