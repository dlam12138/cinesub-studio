#!/usr/bin/env python3
"""Local launcher for CineSub Studio."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

BOOTSTRAP_APP_ROOT = Path(__file__).resolve().parent
SRC = BOOTSTRAP_APP_ROOT / "src"
for sub in ["core", "pipeline", "config", "web", "tools"]:
    subpath = str(SRC / sub)
    if subpath not in sys.path:
        sys.path.insert(0, subpath)

from ffmpeg_locator import find_ffmpeg_info
from runtime_paths import resolve_runtime_paths

PATHS = resolve_runtime_paths(Path(__file__).resolve())
PROJECT_ROOT = PATHS.project_root
APP_ROOT = PATHS.app_root
PYTHON_EXE = Path(sys.executable)
LOG_PATH = PROJECT_ROOT / "logs" / "web_server.log"
PYTHONPATH = PATHS.pythonpath()
DEFAULT_PORT = 7860
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 30.0

process: subprocess.Popen | None = None


def _url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start CineSub Studio local Web UI.")
    parser.add_argument("-NoBrowser", "--no-browser", action="store_true", dest="no_browser")
    parser.add_argument("-Smoke", "--smoke", action="store_true", dest="smoke")
    parser.add_argument(
        "-NonInteractive",
        "--non-interactive",
        action="store_true",
        dest="non_interactive",
    )
    parser.add_argument("-Port", "--port", type=int, default=DEFAULT_PORT, dest="port")
    return parser.parse_args(argv)


def _is_web_ready(port: int) -> bool:
    try:
        with urlopen(_url(port), timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _start_server(port: int) -> subprocess.Popen:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHONPATH
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["SUBTITLE_WEB_PORT"] = str(port)
    cmd = [str(PYTHON_EXE), "-B", "-m", "src.web.web_server"]
    return subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _wait_for_ready(port: int) -> bool:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if _is_web_ready(port):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def _open_browser(port: int) -> None:
    webbrowser.open(_url(port))


def _stop_server() -> None:
    global process
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    process = None


def _print_header(args: argparse.Namespace) -> None:
    print("CineSub Studio local launcher")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python: {PYTHON_EXE}")
    print(f"Local URL: {_url(args.port)}")
    print(f"Server log: {LOG_PATH}")
    print("Runtime diagnostics: open the Web UI and choose the Runtime tab.")


def _check_ffmpeg() -> bool:
    info = find_ffmpeg_info(PROJECT_ROOT)
    if info["ok"]:
        print(f"FFmpeg: found via {info['source']} at {info['path']}")
        print("FFmpeg override variables: CINESUB_FFMPEG, FFMPEG_PATH")
        return True

    print("FFmpeg: not found.")
    print("Media jobs that extract audio will fail until FFmpeg is configured.")
    print("The Web UI can still start for settings and runtime diagnostics.")
    print("Accepted environment variables: CINESUB_FFMPEG, FFMPEG_PATH")
    print(f"Expected project location: {PROJECT_ROOT / 'tools' / 'ffmpeg' / 'bin'}")
    print("Optional helper: .\\scripts\\download_ffmpeg.ps1")
    return False


def _run_smoke(args: argparse.Namespace) -> int:
    print("Smoke mode: non-interactive startup readiness check.")
    ffmpeg_ok = _check_ffmpeg()
    if _is_web_ready(args.port):
        print(f"Smoke result: existing server is responding at {_url(args.port)}")
        return 0
    if _is_port_in_use(args.port):
        print(f"Smoke result: port {args.port} is in use, but CineSub Studio did not return HTTP 200.")
        print("Choose another port with -Port or stop the process using that port.")
        return 2

    print("Smoke result: imports and launcher checks completed.")
    if not ffmpeg_ok:
        print("Smoke warning: FFmpeg is missing, but this does not block Web UI startup.")
    print("No browser was opened. No model download or media processing was started.")
    return 0


def _run_console_until_stopped(port: int) -> None:
    print("Press Ctrl+C to stop CineSub Studio.")
    try:
        while True:
            if process is not None and process.poll() is not None:
                print(f"Server process exited with code {process.returncode}. Check: {LOG_PATH}")
                return
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Stopping CineSub Studio...")
        _stop_server()


def _run_gui(port: int) -> None:
    # Tk is optional for smoke, packaged readiness, and console-only startup.
    # Import it only when the interactive status window is actually requested;
    # this avoids loading Tcl/Tk DLLs in headless and subprocess smoke runs.
    from tkinter import Button, Label, Tk

    root = Tk()
    root.title("CineSub Studio")
    root.geometry("360x150")
    root.resizable(False, False)
    try:
        root.iconbitmap(str(APP_ROOT / "web" / "favicon.ico"))
    except Exception:
        pass

    Label(root, text="CineSub Studio is running", font=("Microsoft YaHei", 14, "bold")).pack(pady=8)
    Label(root, text=_url(port), fg="blue", font=("Microsoft YaHei", 10)).pack()
    Label(root, text="Log: logs/web_server.log", fg="gray", font=("Microsoft YaHei", 9)).pack(pady=2)

    def on_open() -> None:
        _open_browser(port)

    def on_exit() -> None:
        _stop_server()
        root.destroy()

    button_frame = Label(root)
    button_frame.pack(pady=10)
    Button(button_frame, text="Open Browser", command=on_open, width=14).pack(side="left", padx=5)
    Button(button_frame, text="Exit", command=on_exit, width=14).pack(side="left", padx=5)

    root.protocol("WM_DELETE_WINDOW", on_exit)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    global process
    args = _parse_args(argv)
    if args.smoke:
        args.non_interactive = True
        args.no_browser = True

    _print_header(args)
    if args.smoke:
        return _run_smoke(args)

    _check_ffmpeg()

    if _is_web_ready(args.port):
        print(f"Detected an existing CineSub Studio server at {_url(args.port)}")
        if not args.no_browser:
            _open_browser(args.port)
        return 0

    if _is_port_in_use(args.port):
        print(f"Port {args.port} is already in use, but {_url(args.port)} did not respond as CineSub Studio.")
        print("Stop the process using that port, or start with a different -Port value if supported.")
        return 2

    print("Starting local Web server...")
    process = _start_server(args.port)
    print(f"Waiting for server readiness, timeout {POLL_TIMEOUT:.0f}s...")
    if not _wait_for_ready(args.port):
        print(f"Server did not become ready. Check: {LOG_PATH}")
        if process is not None and process.poll() is not None:
            print(f"Server exited with code {process.returncode}.")
        _stop_server()
        return 1

    print(f"CineSub Studio is ready: {_url(args.port)}")
    if not args.no_browser:
        print("Opening browser...")
        _open_browser(args.port)
    else:
        print("NoBrowser was set; browser open skipped.")

    if args.non_interactive:
        _run_console_until_stopped(args.port)
    else:
        try:
            _run_gui(args.port)
        except Exception as exc:
            print(f"Status window unavailable: {exc}")
            _run_console_until_stopped(args.port)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        _stop_server()
