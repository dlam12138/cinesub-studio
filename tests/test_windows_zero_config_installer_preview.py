from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
DESKTOP = ROOT / "desktop"
PACKAGE_JSON = DESKTOP / "package.json"
MAIN_JS = DESKTOP / "main.js"
PRELOAD_JS = DESKTOP / "preload.js"
README = DESKTOP / "README.md"
GITIGNORE = ROOT / ".gitignore"
ACCEPTANCE = ROOT / "acceptance" / "v0_5_windows_zero_config_installer_preview.md"
PACKAGING = ROOT / "packaging" / "windows"
BUILD_SCRIPT = PACKAGING / "build_installer.ps1"
THIRD_PARTY = PACKAGING / "THIRD_PARTY_NOTICES.md"
PREPARE_RUNTIME = PACKAGING / "prepare_runtime.py"
RUNTIME_PATHS = ROOT / "src" / "tools" / "runtime_paths.py"
RUNTIME_ENV = ROOT / "src" / "tools" / "runtime_env.py"
VERSIONING = ROOT / "src" / "tools" / "versioning.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_package_json_has_windows_installer_scripts():
    package = json.loads(_read(PACKAGE_JSON))
    assert package["scripts"]["pack:win"] == "electron-builder --win --dir"
    assert package["scripts"]["dist:win"] == "electron-builder --win nsis"


def test_package_json_has_windows_installer_target():
    package = json.loads(_read(PACKAGE_JSON))
    build = package.get("build", {})
    assert build.get("productName") == "智译字幕工坊"
    assert build.get("appId") == "studio.cinesub.app"
    assert build.get("win", {}).get("target") == "nsis"
    assert build.get("win", {}).get("executableName") == "CineSubStudio"


def test_package_json_has_no_publish_or_auto_update():
    package = json.loads(_read(PACKAGE_JSON))
    build = package.get("build", {})
    assert build.get("publish") is None
    assert "autoUpdate" not in json.dumps(build).lower()
    assert "auto-update" not in json.dumps(build).lower()


def test_package_json_excludes_models_and_runtime_outputs():
    package = json.loads(_read(PACKAGE_JSON))
    files_text = json.dumps(package.get("files", []))
    extra_text = json.dumps(package.get("extraResources", []))
    combined = files_text + extra_text
    assert "models" not in combined.lower() or "README" in combined
    # Ensure no direct model file inclusion
    assert ".pt" not in combined
    assert ".bin" not in combined or "python" in combined


def test_package_json_uses_staged_portable_runtime_and_ships_notices():
    package = json.loads(_read(PACKAGE_JSON))
    resources = package["build"]["extraResources"]
    assert any(
        item.get("from") == "../packaging/windows/runtime" and item.get("to") == "app"
        for item in resources
    )
    assert any(
        item.get("from") == "../packaging/windows/THIRD_PARTY_NOTICES.md"
        and item.get("to") == "app/THIRD_PARTY_NOTICES.md"
        for item in resources
    )
    assert all(item.get("from") != "../.venv" for item in resources)


def test_main_js_has_packaged_mode_detection():
    main = _read(MAIN_JS)
    assert "app.isPackaged" in main
    assert "resolvePackagedPaths" in main
    assert 'path.join(process.resourcesPath, "app")' in main
    assert "CINESUB_PACKAGED_ROOT" in main
    assert "CINESUB_USER_DATA_ROOT" in main


def test_main_js_packaged_mode_uses_bundled_python():
    main = _read(MAIN_JS)
    assert 'path.join(resourcesApp, "python", "python.exe")' in main
    assert "pythonExe" in main
    assert "validatePackagedPaths" in main


def test_main_js_packaged_mode_prepends_ffmpeg_path():
    main = _read(MAIN_JS)
    assert 'path.join(resourcesApp, "tools", "ffmpeg", "bin")' in main
    assert "CINESUB_FFMPEG" in main
    assert "ffmpegBin" in main


def test_main_js_packaged_mode_prepends_cuda_path():
    main = _read(MAIN_JS)
    assert 'path.join(resourcesApp, "tools", "cuda")' in main
    assert "cudaBin" in main


def test_main_js_still_launches_start_app_py():
    main = _read(MAIN_JS)
    assert 'path.join(repoRoot, "start_app.py")' in main
    assert '"--no-browser"' in main
    assert '"--non-interactive"' in main
    assert '"--port"' in main


def test_main_js_has_no_duplicate_web_server_startup():
    main = _read(MAIN_JS)
    assert "src.web.web_server" not in main
    assert "require(\"src/web/web_server\")" not in main
    assert "web_server" not in main


def test_main_js_backend_cleanup_still_exists():
    main = _read(MAIN_JS)
    assert "function stopBackend()" in main
    assert 'app.on("before-quit"' in main
    assert 'app.on("window-all-closed"' in main
    assert 'spawnSync("taskkill"' in main


def test_main_js_handles_backend_spawn_errors_and_redacts_stderr():
    main = _read(MAIN_JS)
    assert 'child.once("error"' in main
    assert 'child.once("spawn"' in main
    assert "await startBackend" in main
    assert "redactBackendText" in main


def test_main_js_folder_picker_still_exists():
    main = _read(MAIN_JS)
    assert 'ipcMain.handle("dialog:select-directory"' in main
    assert "registerDirectoryPicker" in main


def test_runtime_paths_supports_packaged_layout():
    text = _read(RUNTIME_PATHS)
    assert "CINESUB_PACKAGED_ROOT" in text
    assert 'layout="packaged"' in text
    assert "config_root" in text
    assert "models_dir" in text
    assert "output_dir" in text
    assert "user_data_root" not in text  # property name should not leak; check via models_dir etc.


def test_runtime_env_uses_paths_properties():
    text = _read(RUNTIME_ENV)
    assert "PATHS.models_dir" in text or "PATHS.output_dir" in text


def test_packaging_scripts_exist():
    assert BUILD_SCRIPT.exists()
    assert (PACKAGING / "collect_backend.ps1").exists()
    assert (PACKAGING / "collect_runtime.ps1").exists()
    assert THIRD_PARTY.exists()
    assert PREPARE_RUNTIME.exists()


def test_build_uses_output_dir_and_optional_cuda_policy():
    text = _read(BUILD_SCRIPT)
    assert "OutputDir" in text
    assert "config.directories.output" in text
    assert "RequireCuda" in text
    assert "collect_runtime.ps1" in text
    assert "versioning.py" in text
    assert "Release version consumers do not match VERSION" in text


def test_version_file_is_packaged_and_checked_before_installer_build():
    package = json.loads(_read(PACKAGE_JSON))
    resources = json.dumps(package["build"]["extraResources"])
    assert '"VERSION"' in resources
    assert VERSIONING.exists()


def test_runtime_preparer_builds_from_portable_python_not_venv_executable():
    text = _read(PREPARE_RUNTIME)
    assert 'project_root / "tools" / "python"' in text
    assert 'project_root / ".venv"' in text
    assert '_site_packages(venv_root)' in text
    assert 'staged_python / "pyvenv.cfg"' in text
    assert '[str(python_exe), "-I", "-B"' in text


def test_third_party_notices_mentions_cuda_runtime():
    text = _read(THIRD_PARTY)
    assert "CUDA" in text
    assert "cuBLAS" in text or "cublas" in text.lower()
    assert "cuDNN" in text or "cudnn" in text.lower()
    assert "NVIDIA" in text
    assert "FFmpeg" in text


def test_third_party_notices_mentions_driver_not_bundled():
    text = _read(THIRD_PARTY)
    assert "不包含 NVIDIA 显卡驱动" in text or "NVIDIA driver" in text


def test_readme_mentions_packaged_mode():
    readme = _read(README)
    assert "Packaged mode" in readme
    assert "app.isPackaged" in readme or "installer" in readme.lower()


def test_gitignore_ignores_desktop_dist():
    gitignore = _read(GITIGNORE)
    assert "desktop/dist/" in gitignore
    assert "desktop/out/" in gitignore


def test_no_tts_or_dubbing_in_desktop_shell():
    combined = (_read(MAIN_JS) + "\n" + _read(PRELOAD_JS)).lower()
    forbidden = [
        "tts",
        "dubbing",
        "voice clone",
        "lip-sync",
        "model hub",
        "model store",
        "model installer",
        "download_model_file",
    ]
    for marker in forbidden:
        assert marker not in combined


def test_acceptance_doc_exists_for_v05():
    assert ACCEPTANCE.exists()
    text = _read(ACCEPTANCE)
    assert "v0.5" in text or "v0_5" in text
    assert "Windows Zero-Config Installer Preview" in text
    assert "no code signing" in text.lower()
    assert "no auto-update" in text.lower()
    assert "no model bundle" in text.lower() or "models are separate" in text.lower()
    assert "NVIDIA driver" in text or "显卡驱动" in text
