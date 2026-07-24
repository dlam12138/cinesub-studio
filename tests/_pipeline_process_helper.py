"""Cross-process lock/lease test helper for the pipeline reliability tests.

Why this exists (Issue #10): the original process tests spawned children with
``python -c "<code>"`` and relied on the calling terminal's ambient
``PYTHONPATH``. pytest's ``pythonpath`` ini option only patches the pytest
process' ``sys.path`` — children that import project modules then die with a
``ModuleNotFoundError`` on stderr nobody reads, and the tests observe a bare
``'' != 'locked'`` / ``None == 0``. CI injects ``PYTHONPATH`` in the workflow,
so the same tests looked green there and flaky locally.

Child side (``python -u tests/_pipeline_process_helper.py --mode <mode> ...``):
  * self-bootstraps the project import path relative to this file, so children
    never depend on ambient ``PYTHONPATH`` or the inherited cwd;
  * reports protocol state as one-line JSON events on stdout:
    ``started``, ``waiting_for_lease``, ``locked``, ``blocked``, ``leased``,
    ``lease_failed``, ``acked``, ``released``, ``acquired``, ``error``;
  * accepts JSON-line commands on stdin (``{"cmd": "release"}``) for explicit
    handshakes instead of fixed sleeps;
  * writes real errors to stderr only; protocol events never go to stderr.

Parent side (imported by tests — pytest puts ``tests/`` on ``sys.path``):
  * :func:`build_probe_env` — explicit ``PYTHONPATH`` / ``PYTHONUTF8`` /
    ``PYTHONIOENCODING`` / ``PYTHONUNBUFFERED`` child environment;
  * :class:`PipelineProbe` — bounded (``time.monotonic`` deadline) event
    reader with pump threads; failures raise :class:`ProbeError` carrying pid,
    returncode, observed events and stdout/stderr tails instead of ``''``.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_SUBDIRS = ("core", "pipeline", "config", "web", "tools")
HELPER_PATH = Path(__file__).resolve()

OUTPUT_LIMIT = 4000
MAX_EVENTS = 500


def src_pythonpath() -> str:
    return os.pathsep.join(str(REPO_ROOT / "src" / name) for name in SRC_SUBDIRS)


def build_probe_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Deterministic child environment independent of the caller's shell."""
    env = dict(base if base is not None else os.environ)
    env["PYTHONPATH"] = src_pythonpath()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class ProbeError(AssertionError):
    """Diagnostic failure raised when a child probe misbehaves or times out."""


STILL_ACTIVE = 259


def process_exit_code(pid: int) -> int | None:
    """Authoritative process state on Windows.

    Returns ``STILL_ACTIVE`` (259) while the process runs, its exit code
    once it has exited, and ``None`` when the process object is gone.
    Unlike ``OpenProcess`` + ``GetProcessTimes`` (creation filetime), this
    is not fooled by lingering inherited handles that keep a dead process
    object queryable for a short while after termination.
    """
    if os.name != "nt" or not pid:
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return int(code.value)
    finally:
        kernel32.CloseHandle(handle)


def process_is_alive(pid: int) -> bool:
    return process_exit_code(pid) == STILL_ACTIVE


def _tail(text: str, limit: int = OUTPUT_LIMIT) -> str:
    text = text or ""
    return ("..." + text[-limit:]) if len(text) > limit else text


class PipelineProbe:
    """Spawn the helper child and drive it via an explicit JSON-line protocol."""

    def __init__(self, *args: Any, env: dict[str, str] | None = None):
        self.args = [str(value) for value in args]
        self.command = [sys.executable, "-u", str(HELPER_PATH), *self.args]
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            env=build_probe_env(env),
        )
        self.events: list[dict[str, Any]] = []
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self._raw_stdout: list[str] = []
        self._raw_stderr: list[str] = []
        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, daemon=True, name="probe-stdout"
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, daemon=True, name="probe-stderr"
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._cleaned = False

    # ------------------------------------------------------------------ pumps
    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._raw_stdout.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"event": "raw_stdout", "text": line.rstrip("\r\n")}
            self._queue.put(event)
        self._queue.put(None)  # EOF sentinel

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        self._raw_stderr.append(self.proc.stderr.read())

    # ------------------------------------------------------------- diagnostics
    def _diagnostics(self, reason: str) -> str:
        self._stderr_thread.join(timeout=1.0)
        try:
            events_text = json.dumps(self.events, ensure_ascii=False)
        except (TypeError, ValueError):
            events_text = repr(self.events)
        return "\n".join([
            f"pipeline probe failure: {reason}",
            f"  command: python -u tests/_pipeline_process_helper.py {' '.join(self.args)}",
            f"  pid: {self.proc.pid}",
            f"  returncode: {self.proc.poll()}",
            f"  events: {_tail(events_text)}",
            f"  stdout tail: {_tail(''.join(self._raw_stdout))!r}",
            f"  stderr tail: {_tail(''.join(self._raw_stderr))!r}",
        ])

    def _raise(self, reason: str) -> None:
        self.cleanup()
        raise ProbeError(self._diagnostics(reason))

    # ------------------------------------------------------------ parent API
    def wait_for_event(
        self, expected: str, timeout: float = 15.0, **match: Any
    ) -> dict[str, Any]:
        """Wait for one JSON event matching ``expected`` (and ``match`` fields).

        Bounded by a monotonic deadline; never blocks forever in readline().
        On EOF, timeout, or a child ``error`` event, raises :class:`ProbeError`
        with pid / returncode / events / stderr tail — never a bare ''.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._raise(
                    f"timed out after {timeout:.1f}s waiting for event "
                    f"{expected!r} {match or ''}"
                )
            try:
                event = self._queue.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self.proc.poll() is not None and self._queue.empty():
                    self._raise(
                        f"child exited with returncode={self.proc.returncode} "
                        f"before emitting event {expected!r}"
                    )
                continue
            if event is None:
                self._raise(
                    f"stdout EOF before event {expected!r} "
                    f"(returncode={self.proc.poll()})"
                )
            self.events.append(event)
            if len(self.events) > MAX_EVENTS:
                self._raise(
                    f"more than {MAX_EVENTS} events without reaching {expected!r}"
                )
            if event.get("event") == "error" and expected != "error":
                self._raise(
                    "child reported error while waiting for "
                    f"{expected!r}: {event.get('message')}"
                )
            if event.get("event") == expected and all(
                event.get(key) == value for key, value in match.items()
            ):
                return event

    def send_command(self, cmd: str, **payload: Any) -> None:
        if self.proc.poll() is not None:
            self._raise(
                f"cannot send command {cmd!r}: child already exited "
                f"(returncode={self.proc.returncode})"
            )
        assert self.proc.stdin is not None
        line = json.dumps({"cmd": cmd, **payload}, ensure_ascii=False) + "\n"
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._raise(f"failed to send command {cmd!r}: {exc}")

    def wait_for_exit(self, timeout: float = 15.0, expected: int | None = 0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.proc.poll()
            if code is not None:
                if expected is not None and code != expected:
                    self._raise(
                        f"child exited with returncode={code}, expected {expected}"
                    )
                return code
            time.sleep(0.01)
        self._raise(f"child did not exit within {timeout:.1f}s")

    def release_and_wait(
        self, timeout: float = 15.0, expected_exit_code: int = 0
    ) -> None:
        self.send_command("release")
        self.wait_for_event("released", timeout=timeout)
        self.wait_for_exit(timeout=timeout, expected=expected_exit_code)

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        if self.proc.poll() is None:
            # Prefer the child's own exit code: a Windows venv python.exe is a
            # launcher that spawns the real interpreter (the pid reported in
            # the events) and forwards its exit code. Killing the launcher too
            # early masks the child's true returncode and can orphan the
            # interpreter process, so first give it a moment to exit itself.
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        if self.proc.poll() is None:
            # EOF on stdin unblocks children waiting for a release command.
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                pass
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------

def _emit(event: dict[str, Any]) -> None:
    event.setdefault("pid", os.getpid())
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _read_command() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        _emit({"event": "error", "stage": "stdin", "message": "stdin closed before command"})
        raise SystemExit(1)
    try:
        command = json.loads(line)
    except json.JSONDecodeError as exc:
        _emit({"event": "error", "stage": "stdin", "message": f"bad command line: {exc}"})
        raise SystemExit(1)
    return command if isinstance(command, dict) else {"cmd": command}


def _bootstrap_imports() -> Any:
    """Make project modules importable regardless of ambient PYTHONPATH/cwd."""
    for name in SRC_SUBDIRS:
        entry = str(REPO_ROOT / "src" / name)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        from pipeline_reliability import PipelineRunLock

        return PipelineRunLock
    except Exception as exc:
        _emit({
            "event": "error",
            "stage": "import",
            "message": f"{type(exc).__name__}: {exc}",
            "exc": traceback.format_exc(limit=8)[-1500:],
        })
        raise SystemExit(2)


def _run_child(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="pipeline-lock-probe")
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "lock",
            "lease-worker",
            "launcher",
            "fake-worker",
            "abrupt-exit",
            "fail-import",
            "fail-runtime",
            "hang-before-lease",
        ],
    )
    parser.add_argument("--lock-path", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--acquire-timeout", type=float, default=0.0)
    parser.add_argument("--ack-path", default="")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    parser.add_argument("--pid-file", default="")
    parser.add_argument("--sentinel", default="")
    args = parser.parse_args(argv)

    _emit({"event": "started", "mode": args.mode})

    if args.mode == "fail-import":
        try:
            import no_such_probe_module_xyz  # noqa: F401
        except ModuleNotFoundError as exc:
            _emit({
                "event": "error",
                "stage": "import",
                "message": f"{type(exc).__name__}: {exc}",
                "exc": traceback.format_exc(limit=8)[-1500:],
            })
            return 2
        return 0

    if args.mode == "fail-runtime":
        raise RuntimeError("probe intentional runtime failure")

    PipelineRunLock = _bootstrap_imports()

    lock_path = args.lock_path or os.environ.get("CINESUB_PIPELINE_LOCK_PATH", "")

    if args.mode == "lock":
        lock = PipelineRunLock(Path(lock_path), offset=args.offset)
        acquired = lock.acquire()
        if not acquired and args.acquire_timeout > 0:
            deadline = time.monotonic() + args.acquire_timeout
            while not acquired and time.monotonic() < deadline:
                time.sleep(0.02)
                acquired = lock.acquire()
        if not acquired:
            _emit({"event": "blocked", "offset": args.offset})
            return 0
        _emit({"event": "locked", "offset": args.offset})
        command = _read_command()
        if command.get("cmd") != "release":
            _emit({
                "event": "error",
                "stage": "command",
                "message": f"unexpected command {command!r}",
            })
            lock.release()
            return 1
        lock.release()
        _emit({"event": "released", "offset": args.offset})
        return 0

    if args.mode == "lease-worker":
        _emit({"event": "waiting_for_lease", "offset": args.offset})
        lease = PipelineRunLock(Path(lock_path), offset=args.offset)
        deadline = time.monotonic() + max(args.acquire_timeout, 5.0)
        acquired = False
        while time.monotonic() < deadline and not acquired:
            acquired = lease.acquire()
            if not acquired:
                time.sleep(0.02)
        if not acquired:
            _emit({"event": "lease_failed", "offset": args.offset})
            return 3
        _emit({"event": "leased", "offset": args.offset})
        command = _read_command()
        if command.get("cmd") != "release":
            _emit({
                "event": "error",
                "stage": "command",
                "message": f"unexpected command {command!r}",
            })
            lease.release()
            return 1
        lease.release()
        _emit({"event": "released", "offset": args.offset})
        return 0

    if args.mode == "launcher":
        # Mirror start_pipeline_background: take gate (offset 0) then worker
        # lease (offset 1); all-or-nothing, release on failure.
        held = []
        blocking = -1
        for offset in (0, 1):
            candidate = PipelineRunLock(Path(lock_path), offset=offset)
            if candidate.acquire():
                held.append(candidate)
            else:
                blocking = offset
                break
        if blocking == -1:
            _emit({"event": "acquired", "offsets": [0, 1]})
            for item in reversed(held):
                item.release()
            _emit({"event": "released", "offsets": [0, 1]})
        else:
            for item in reversed(held):
                item.release()
            _emit({"event": "blocked", "offset": blocking})
        return 0

    if args.mode == "fake-worker":
        # Mirrors the batch_worker handoff contract: poll the worker lease,
        # write the ack (path from --ack-path or CINESUB_PIPELINE_LOCK_ACK),
        # hold the lease for --hold-seconds, release, exit with --exit-code.
        ack_path = args.ack_path or os.environ.get("CINESUB_PIPELINE_LOCK_ACK", "")
        lease = PipelineRunLock(Path(lock_path), offset=args.offset)
        deadline = time.monotonic() + max(args.acquire_timeout, 10.0)
        acquired = False
        while time.monotonic() < deadline and not acquired:
            acquired = lease.acquire()
            if not acquired:
                time.sleep(0.02)
        if not acquired:
            _emit({"event": "lease_failed", "offset": args.offset})
            return 3
        _emit({"event": "leased", "offset": args.offset})
        if ack_path:
            Path(ack_path).write_text(str(os.getpid()), encoding="ascii")
            _emit({"event": "acked", "offset": args.offset})
        if args.hold_seconds > 0:
            time.sleep(args.hold_seconds)
        lease.release()
        _emit({"event": "released", "offset": args.offset})
        return args.exit_code

    if args.mode == "abrupt-exit":
        lock = PipelineRunLock(Path(lock_path), offset=args.offset)
        if not lock.acquire():
            _emit({"event": "blocked", "offset": args.offset})
            return 3
        _emit({"event": "locked", "offset": args.offset})
        os._exit(args.exit_code)  # no cleanup: the OS must release the lock

    if args.mode == "hang-before-lease":
        # Simulates a worker stuck before it acquires the lease / writes the
        # ack (e.g. a slow import under Defender). Reports the *real*
        # interpreter pid — under a Windows venv this differs from the
        # parent's Popen.pid, which is the forwarding launcher — then hangs
        # until the test writes the sentinel file or a bounded deadline
        # expires, so a missed kill self-cleans instead of leaking forever.
        # Never touches the lock file and never writes the ack.
        if args.pid_file:
            Path(args.pid_file).write_text(str(os.getpid()), encoding="ascii")
        _emit({"event": "hanging", "reported_pid": os.getpid()})
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            if args.sentinel and Path(args.sentinel).exists():
                return 0
            time.sleep(0.05)
        return 4

    _emit({"event": "error", "stage": "mode", "message": f"unhandled mode {args.mode}"})
    return 1


def _child_main(argv: list[str]) -> int:
    try:
        return _run_child(argv)
    except SystemExit:
        raise
    except Exception as exc:
        _emit({
            "event": "error",
            "stage": "runtime",
            "message": f"{type(exc).__name__}: {exc}",
            "exc": traceback.format_exc(limit=8)[-1500:],
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
