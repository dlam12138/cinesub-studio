#!/usr/bin/env python3
"""CineSub Studio 启动器。

双击运行即可启动 Web 服务并自动打开浏览器。
无需手动打开 PowerShell 或记住命令。

用法:
    py start_app.py
    或双击 start_app.py

功能:
    1. 启动 web_server.py 子进程
    2. 等待服务就绪（轮询 http://127.0.0.1:7860）
    3. 自动打开浏览器
    4. 显示托盘/状态窗口，方便退出
    5. 关闭时优雅停止子进程
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import threading
import webbrowser
from pathlib import Path
from tkinter import Button, Label, Tk
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = Path(sys.executable)
LOG_PATH = PROJECT_ROOT / "logs" / "web_server.log"

# Build PYTHONPATH that includes all src subdirectories for cross-module imports
_SRC = PROJECT_ROOT / "src"
_PYTHONPATH = ";".join(str(_SRC / sub) for sub in ["core", "pipeline", "config", "web", "tools"])
for _sub in ["core", "pipeline", "config", "web", "tools"]:
    _subpath = str(_SRC / _sub)
    if _subpath not in sys.path:
        sys.path.insert(0, _subpath)

from ffmpeg_locator import find_ffmpeg

PORT = 7860
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 30.0

_process: subprocess.Popen | None = None

def _is_port_ready() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/", timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_server() -> subprocess.Popen:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = _PYTHONPATH
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [str(PYTHON_EXE), "-B", "-m", "src.web.web_server"],
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return proc


def _wait_for_ready() -> bool:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        if _is_port_ready():
            return True
        time.sleep(POLL_INTERVAL)
    return False


def _open_browser() -> None:
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


def _stop_server() -> None:
    global _process
    if _process is not None:
        try:
            _process.terminate()
            _process.wait(timeout=5)
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass
        _process = None


def main() -> int:
    global _process

    # ── Check ffmpeg ──
    ffmpeg_ok = _check_ffmpeg()
    if not ffmpeg_ok:
        # Show a simple dialog explaining the situation
        try:
            from tkinter import messagebox
            result = messagebox.askyesno(
                "需要 ffmpeg",
                "ffmpeg 未找到。\n\n"
                "CineSub Studio 需要 ffmpeg 来提取音频。\n\n"
                "是否自动下载？（约 40 MB，下载到 tools/ 目录）\n\n"
                "如果不下载，可以手动安装 ffmpeg 并添加到 PATH。"
            )
            if result:
                download_script = PROJECT_ROOT / "src" / "tools" / "download_ffmpeg.py"
                if download_script.exists():
                    import subprocess
                    subprocess.run([sys.executable, "-B", str(download_script)])
                    # Re-check after download
                    if not _check_ffmpeg():
                        messagebox.showerror("下载失败", "ffmpeg 下载失败。请检查网络或手动安装。")
                        return 1
                else:
                    messagebox.showerror("错误", "download_ffmpeg.py 不存在。")
                    return 1
            else:
                # User chose not to download; try to proceed anyway
                pass
        except Exception:
            # tkinter not available or failed, fall back to console
            print("ffmpeg not found. Run: py download_ffmpeg.py")
            return 1

    # 检查是否已有服务在运行
    if _is_port_ready():
        print(f"检测到 http://127.0.0.1:{PORT}/ 已运行，直接打开浏览器...")
        _open_browser()
        return 0

    # 启动服务
    print("正在启动 CineSub Studio Web 服务...")
    _process = _start_server()

    # 等待就绪
    print(f"等待服务就绪（超时 {POLL_TIMEOUT}s）...")
    if not _wait_for_ready():
        print("服务启动超时，请检查 logs/web_server.log")
        _stop_server()
        return 1

    print(f"服务已就绪: http://127.0.0.1:{PORT}/")
    _open_browser()

    # 启动 GUI 状态窗口
    _run_gui()
    return 0


def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available in the project or PATH."""
    return find_ffmpeg(PROJECT_ROOT) is not None


def _run_gui() -> None:
    """显示一个最小化状态窗口，方便用户查看和退出。"""
    root = Tk()
    root.title("CineSub Studio")
    root.geometry("320x140")
    root.resizable(False, False)

    # 尝试设置图标（如果有的话）
    try:
        root.iconbitmap(str(PROJECT_ROOT / "web" / "favicon.ico"))
    except Exception:
        pass

    Label(root, text="CineSub Studio 正在运行", font=("Microsoft YaHei", 14, "bold")).pack(pady=8)
    Label(root, text=f"http://127.0.0.1:{PORT}/", fg="blue", font=("Microsoft YaHei", 10)).pack()
    Label(root, text=f"日志: logs/web_server.log", fg="gray", font=("Microsoft YaHei", 9)).pack(pady=2)

    def _on_open() -> None:
        _open_browser()

    def _on_exit() -> None:
        _stop_server()
        root.destroy()

    btn_frame = Label(root)
    btn_frame.pack(pady=10)

    Button(btn_frame, text="打开浏览器", command=_on_open, width=12).pack(side="left", padx=5)
    Button(btn_frame, text="退出", command=_on_exit, width=12).pack(side="left", padx=5)

    root.protocol("WM_DELETE_WINDOW", _on_exit)
    root.mainloop()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _stop_server()
        raise SystemExit(0)
