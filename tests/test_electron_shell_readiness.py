from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
DESKTOP = ROOT / "desktop"
PACKAGE_JSON = DESKTOP / "package.json"
PACKAGE_LOCK = DESKTOP / "package-lock.json"
MAIN_JS = DESKTOP / "main.js"
LAUNCH_JS = DESKTOP / "launch.js"
PRELOAD_JS = DESKTOP / "preload.js"
README = DESKTOP / "README.md"
GITIGNORE = ROOT / ".gitignore"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_package_has_minimal_electron_start_script():
    package = json.loads(_read(PACKAGE_JSON))
    assert package["name"] == "cinesub-studio-desktop"
    assert package["version"] == "0.6.2"
    assert package["private"] is True
    assert package["main"] == "main.js"
    assert package["scripts"]["start"] == "node launch.js"
    assert package["scripts"]["pack:win"] == "electron-builder --win --dir"
    assert "dist:win" not in package["scripts"]
    assert package["devDependencies"]["electron"] == "37.10.3"
    assert "electron-builder" in json.dumps(package).lower()
    assert package["build"]["win"]["target"] == "dir"
    assert package["build"]["productName"] == "智译字幕工坊"
    assert package["build"]["appId"] == "studio.cinesub.app"
    # No auto-update / publish for preview
    assert package["build"].get("publish") is None
    assert "nsis" not in package["build"]


def test_package_lock_exists_and_locks_electron():
    assert PACKAGE_LOCK.exists()
    lock = json.loads(_read(PACKAGE_LOCK))
    assert lock["packages"][""]["devDependencies"]["electron"] == "37.10.3"
    assert "node_modules/electron" in lock["packages"]


def test_main_and_preload_exist_with_only_folder_picker_bridge():
    assert MAIN_JS.exists()
    assert PRELOAD_JS.exists()
    preload = _read(PRELOAD_JS)
    assert 'contextBridge.exposeInMainWorld("cineSubDesktop"' in preload
    assert "selectDirectory" in preload
    assert 'ipcRenderer.invoke("dialog:select-directory")' in preload
    assert "ipcRenderer.send" not in preload
    assert "ipcRenderer.on" not in preload
    assert "require(\"node:fs\")" not in preload
    assert "require(\"node:child_process\")" not in preload
    assert "require(\"fs\")" not in preload
    assert "require(\"child_process\")" not in preload
    assert "api_key" not in preload.lower()
    assert "token" not in preload.lower()
    assert "secret" not in preload.lower()


def test_main_starts_existing_python_launcher_with_desktop_flags():
    main = _read(MAIN_JS)
    assert 'path.resolve(__dirname, "..")' in main
    assert "CINESUB_REPO_ROOT" in main
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
    # v0.5 packaged mode
    assert "app.isPackaged" in main
    assert "resolvePackagedPaths" in main
    assert 'path.join(process.resourcesPath, "app")' in main
    assert "CINESUB_PACKAGED_ROOT" in main


def test_launch_stages_electron_app_outside_non_ascii_project_path():
    assert LAUNCH_JS.exists()
    launch = _read(LAUNCH_JS)
    assert 'path.join(os.tmpdir(), "cinesub-studio-electron-app")' in launch
    assert 'for (const fileName of ["main.js", "preload.js"])' in launch
    assert 'CINESUB_REPO_ROOT: repoRoot' in launch
    assert 'spawn(electronPath, [stagedAppDir]' in launch
    assert 'stdio: "inherit"' in launch


def test_main_port_handling_does_not_reuse_or_fallback_silently():
    main = _read(MAIN_JS)
    assert "CINESUB_DESKTOP_PORT" in main
    assert "DEFAULT_PORT = 7860" in main
    assert 'return "cinesub";' in main
    assert 'return "occupied";' in main
    assert 'return "available";' in main
    assert "智译字幕工坊 / CineSub Studio is already running" in main
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
    assert 'spawnSync("taskkill", ["/pid", String(backendProcess.pid), "/T", "/F"]' in main
    assert "backendProcess.kill();" in main
    assert 'app.on("before-quit"' in main
    assert 'app.on("window-all-closed"' in main
    forbidden = ["wmic", "Get-Process", "pkill", "killall"]
    for marker in forbidden:
        assert marker not in main


def test_main_uses_secure_browser_window_and_external_link_handling():
    main = _read(MAIN_JS)
    assert "width: 1440" in main
    assert "height: 900" in main
    assert "minWidth: 1100" in main
    assert "minHeight: 720" in main
    assert 'title: "智译字幕工坊 / CineSub Studio"' in main
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert 'preload: path.join(__dirname, "preload.js")' in main
    assert "setWindowOpenHandler" in main
    assert "will-navigate" in main
    assert "shell.openExternal" in main
    assert 'parsed.protocol === "https:"' in main
    assert main.count("shell.openExternal(") == 1


def test_no_raw_api_keys_or_future_media_features_in_desktop_shell():
    combined = (_read(MAIN_JS) + "\n" + _read(PRELOAD_JS) + "\n" + _read(README)).lower()
    forbidden = [
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
    assert "redactbackendtext" in combined
    assert "sk-***" in combined


def test_readme_documents_setup_and_limitations():
    readme = _read(README)
    assert ".\\start_web.ps1 -Smoke -NoBrowser -NonInteractive" in readme
    assert "npm install" in readme
    assert "npm start" in readme
    assert "开发模式使用项目 `.venv`" in readme
    assert "不提供代码签名" in readme
    assert "自动更新" in readme
    assert "正式构建统一从仓库根目录调用" in readme


def test_gitignore_keeps_desktop_runtime_and_internal_artifacts_out():
    gitignore = _read(GITIGNORE)
    for marker in (
        "desktop/node_modules/",
        "desktop/dist/",
        "desktop/out/",
        "desktop/.vite/",
        "desktop/*.log",
    ):
        assert marker in gitignore
    assert "acceptance/" in gitignore
    assert ".superdesign/" in gitignore
    assert "research/" in gitignore
