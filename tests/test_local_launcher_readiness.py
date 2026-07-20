from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
START_WEB = ROOT / "start_web.ps1"
START_APP = ROOT / "start_app.py"
DESIGN_NOTE = ROOT / "docs" / "desktopization_readiness.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_start_web_documents_launcher_modes():
    text = _read(START_WEB)
    assert "$NoBrowser" in text
    assert "$Smoke" in text
    assert "$NonInteractive" in text
    assert "$Port" in text
    assert "http://127.0.0.1:$Port/" in text


def test_start_app_smoke_is_non_interactive_and_browser_can_be_disabled():
    text = _read(START_APP)
    assert "-Smoke" in text
    assert "-NoBrowser" in text
    assert "-NonInteractive" in text
    assert "Smoke mode: non-interactive startup readiness check." in text
    assert "No browser was opened" in text
    assert "messagebox" not in text
    assert "askyesno" not in text
    assert "from tkinter import Button, Label, Tk" not in text.split("def _run_gui", 1)[0]


def test_ffmpeg_missing_message_mentions_supported_configuration_paths():
    text = _read(START_APP) + "\n" + _read(START_WEB)
    assert "CINESUB_FFMPEG" in text
    assert "FFMPEG_PATH" in text
    assert "tools" in text
    assert "ffmpeg" in text.lower()


def test_startup_scripts_do_not_trigger_model_downloads_or_future_media_features():
    lower = (_read(START_APP) + "\n" + _read(START_WEB)).lower()
    forbidden = [
        "download_model_file",
        "huggingface_hub",
        "snapshot_download",
        "model hub",
        "dubbing",
        "tts",
        "voice clone",
        "lip-sync",
    ]
    for marker in forbidden:
        assert marker not in lower


def test_smoke_command_returns_without_user_input_or_browser_launch():
    result = subprocess.run(
        [sys.executable, "-B", str(START_APP), "-Smoke", "-NoBrowser", "-NonInteractive"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert result.returncode == 0
    output = result.stdout + result.stderr
    assert "Smoke mode" in output
    assert "No browser was opened" in output
    assert "No model download" in output
    assert "CINESUB_FFMPEG" in output
    assert "FFMPEG_PATH" in output


def test_desktopization_design_note_exists_and_defers_shells():
    text = _read(DESIGN_NOTE)
    assert "Browser launcher" in text
    assert "Electron" in text
    assert "Tauri" in text
    assert "Defer Electron/Tauri" in text
    assert "No installer in M13" in text
