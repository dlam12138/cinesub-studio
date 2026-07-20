from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parent.parent
MAIN_JS = ROOT / "desktop" / "main.js"
PRELOAD_JS = ROOT / "desktop" / "preload.js"
INDEX_HTML = ROOT / "web" / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_main_registers_narrow_directory_picker_ipc():
    main = _read(MAIN_JS)
    assert 'ipcMain.handle("dialog:select-directory"' in main
    assert "dialog.showOpenDialog(mainWindow" in main
    assert 'properties: ["openDirectory"]' in main
    assert "result.filePaths[0]" in main
    assert "registerDirectoryPicker();" in main
    assert main.index("registerDirectoryPicker();") < main.index("return main();")


def test_preload_exposes_only_select_directory_bridge():
    preload = _read(PRELOAD_JS)
    assert 'const { contextBridge, ipcRenderer } = require("electron");' in preload
    assert 'contextBridge.exposeInMainWorld("cineSubDesktop", {' in preload
    assert "selectDirectory: () => ipcRenderer.invoke(\"dialog:select-directory\")" in preload
    assert "ipcRenderer.send" not in preload
    assert "ipcRenderer.on" not in preload
    assert "exposeInMainWorld" in preload
    assert preload.count("selectDirectory") == 1


def test_preload_does_not_expose_node_or_secrets():
    preload = _read(PRELOAD_JS).lower()
    forbidden = (
        "require(\"fs\")",
        "require('fs')",
        "require(\"node:fs\")",
        "require('node:fs')",
        "child_process",
        "api_key",
        "token",
        "secret",
        "provider",
    )
    for marker in forbidden:
        assert marker not in preload


def test_web_adds_desktop_only_folder_picker_for_pipeline_input():
    html = _read(INDEX_HTML)
    assert 'id="pipelineInputDir"' in html
    assert 'id="pipelineDirectoryPickerBtn"' in html
    assert "选择文件夹" in html
    assert "window.cineSubDesktop?.selectDirectory" in html
    assert "setupDesktopDirectoryPicker();" in html
    assert "await window.cineSubDesktop.selectDirectory()" in html
    assert "inputDirEl.value = selectedPath;" in html


def test_browser_mode_keeps_manual_input_and_hides_picker_by_default():
    html = _read(INDEX_HTML)
    assert '<input id="pipelineInputDir" type="text" value="input" placeholder="input 或 D:\\Movies">' in html
    assert '<button id="pipelineDirectoryPickerBtn" class="directory-picker-btn" type="button" hidden disabled>' in html
    assert "btn.hidden = true;" in html
    assert "btn.disabled = true;" in html
    assert "btn.hidden = false;" in html
    assert "btn.disabled = false;" in html


def test_pipeline_actions_and_request_shape_are_unchanged():
    html = _read(INDEX_HTML)
    assert "pipelineAction('scan')" in html
    assert "pipelineAction('run')" in html
    assert "var url = '/api/pipeline/' + urlPath;" in html
    assert "var body = { input_dir: input_dir };" in html
    assert "fetchOpts.body = JSON.stringify(body);" in html


def test_no_future_media_or_downloader_surface_was_added():
    combined = (_read(MAIN_JS) + "\n" + _read(PRELOAD_JS) + "\n" + _read(INDEX_HTML)).lower()
    forbidden = (
        "model hub",
        "model store",
        "model installer",
        "downloader",
        "download_model_file",
        "dubbing",
        "tts",
        "voice clone",
        "lip-sync",
    )
    for marker in forbidden:
        assert marker not in combined

    desktop_bridge = (_read(MAIN_JS) + "\n" + _read(PRELOAD_JS)).lower()
    assert "api key" not in desktop_bridge
    assert "api_key" not in desktop_bridge
