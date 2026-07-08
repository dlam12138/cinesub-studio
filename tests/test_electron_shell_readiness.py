from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
DESKTOP = ROOT / "desktop"
PACKAGE_JSON = DESKTOP / "package.json"
PACKAGE_LOCK = DESKTOP / "package-lock.json"
MAIN_JS = DESKTOP / "main.js"
PRELOAD_JS = DESKTOP / "preload.js"
README = DESKTOP / "README.md"
GITIGNORE = ROOT / ".gitignore"
ACCEPTANCE = ROOT / "acceptance" / "v0_3_electron_desktop_shell_mvp.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_package_has_minimal_electron_start_script():
    package = json.loads(_read(PACKAGE_JSON))
    assert package["name"] == "cinesub-studio-desktop"
    assert package["version"] == "0.3.0"
    assert package["private"] is True
    assert package["main"] == "main.js"
    assert package["scripts"] == {"start": "electron ."}
    assert package["devDependencies"]["electron"] == "43.0.0"
    forbidden_scripts = {"pack", "package", "dist", "release", "make", "publish"}
    assert not forbidden_scripts.intersection(package["scripts"])
    assert "electron-builder" not in json.dumps(package).lower()


def test_package_lock_exists_and_locks_electron():
    assert PACKAGE_LOCK.exists()
    lock = json.loads(_read(PACKAGE_LOCK))
    assert lock["packages"][""]["devDependencies"]["electron"] == "43.0.0"
    assert "node_modules/electron" in lock["packages"]


def test_main_and_preload_exist_with_no_node_api_exposure():
    assert MAIN_JS.exists()
    assert PRELOAD_JS.exists()
    preload = _read(PRELOAD_JS)
    assert "contextBridge" not in preload
    assert "ipcRenderer" not in preload
    assert "api_key" not in preload.lower()
    assert "token" not in preload.lower()
    assert "secret" not in preload.lower()


def test_main_starts_existing_python_launcher_with_desktop_flags():
    main = _read(MAIN_JS)
    assert 'path.resolve(__dirname, "..")' in main
    assert '".venv", "Scripts", "python.exe"' in main
    assert '".venv", "bin", "python"' in main
    assert 'commandWorks("python", ["--version"])' in main
    assert 'commandWorks("py", ["-3", "--version"])' in main
    assert 'path.join(repoRoot, "start_app.py")' in main
    assert '"-B"' in main
    assert '"--no-browser"' in main
    assert '"--non-interactive"' in main
    assert '"--port"' in main
    assert '"src.web.web_server"' not in main


def test_main_port_handling_does_not_reuse_or_fallback_silently():
    main = _read(MAIN_JS)
    assert "CINESUB_DESKTOP_PORT" in main
    assert "DEFAULT_PORT = 7860" in main
    assert 'return "cinesub";' in main
    assert 'return "occupied";' in main
    assert 'return "available";' in main
    assert "CineSub Studio is already running" in main
    assert "Port is already in use" in main
    assert "fallback" not in main.lower()


def test_main_waits_for_readiness_before_loading_window():
    main = _read(MAIN_JS)
    assert "READINESS_TIMEOUT_MS = 30000" in main
    assert "async function waitForReady(port)" in main
    assert "response.statusCode === 200" in main
    assert "await waitForReady(port);" in main
    assert main.index("await waitForReady(port);") < main.index("createWindow(port);")
    assert "mainWindow.loadURL(appUrl(port));" in main


def test_main_terminates_backend_on_quit_without_process_scanning():
    main = _read(MAIN_JS)
    assert "let backendProcess = null;" in main
    assert "function stopBackend()" in main
    assert "backendProcess.kill();" in main
    assert 'app.on("before-quit"' in main
    assert 'app.on("window-all-closed"' in main
    forbidden = ["taskkill", "wmic", "Get-Process", "pkill", "killall"]
    for marker in forbidden:
        assert marker not in main


def test_main_uses_secure_browser_window_and_external_link_handling():
    main = _read(MAIN_JS)
    assert "width: 1440" in main
    assert "height: 900" in main
    assert "minWidth: 1100" in main
    assert "minHeight: 720" in main
    assert 'title: "CineSub Studio"' in main
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert 'preload: path.join(__dirname, "preload.js")' in main
    assert "setWindowOpenHandler" in main
    assert "will-navigate" in main
    assert "shell.openExternal" in main


def test_no_raw_api_keys_or_future_media_features_in_desktop_shell():
    combined = (_read(MAIN_JS) + "\n" + _read(PRELOAD_JS) + "\n" + _read(README)).lower()
    forbidden = [
        "sk-",
        "raw api key",
        "download_model_file",
        "model hub",
        "model store",
        "model installer",
        "dubbing",
        "tts",
        "voice clone",
        "lip-sync",
        "auto-update script",
    ]
    for marker in forbidden:
        assert marker not in combined


def test_readme_documents_setup_and_limitations():
    readme = _read(README)
    assert ".\\start_web.ps1 -Smoke -NoBrowser -NonInteractive" in readme
    assert "npm install" in readme
    assert "npm start" in readme
    assert "Python and the project `.venv` are still required." in readme
    assert "FFmpeg is not bundled." in readme
    assert "Models are not bundled." in readme
    assert "There is no installer yet." in readme
    assert "There is no code signing." in readme
    assert "There is no auto-update." in readme
    assert "This is not an official release package." in readme


def test_gitignore_keeps_desktop_runtime_artifacts_out_and_acceptance_in():
    gitignore = _read(GITIGNORE)
    for marker in (
        "desktop/node_modules/",
        "desktop/dist/",
        "desktop/out/",
        "desktop/.vite/",
        "desktop/*.log",
    ):
        assert marker in gitignore
    assert "!acceptance/v0_3_electron_desktop_shell_mvp.md" in gitignore


def test_acceptance_note_exists_with_required_evidence_sections():
    assert ACCEPTANCE.exists()
    text = _read(ACCEPTANCE)
    for marker in (
        "Starting commit",
        "Branch",
        "Backend launch command",
        "Python resolution behavior",
        "Port behavior",
        "Server readiness behavior",
        "Window behavior",
        "Backend shutdown behavior",
        "Manual Electron smoke result",
        "Electron resolved version",
        "No installer",
        "No bundled Python",
        "No bundled FFmpeg",
        "No bundled models",
        "No pipeline changes",
        "No ASR or translation behavior changes",
        "No Provider/Profile ownership changes",
    ):
        assert marker in text
