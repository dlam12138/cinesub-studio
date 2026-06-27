from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PROJECT_ROOT / "web"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "output"
MODEL_DIR = PROJECT_ROOT / "models"
WORK_DIR = PROJECT_ROOT / "work"
PIPELINE_LOG = PROJECT_ROOT / "logs" / "pipeline.log"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# Pipeline background task tracking
PIPELINE_TASK: dict = {"running": False, "pid": None, "action": "", "started_at": 0}
PIPELINE_TASK_LOCK = threading.Lock()


def main() -> int:
    host = "127.0.0.1"
    port = int(os.environ.get("SUBTITLE_WEB_PORT", "7860"))

    for path in (UPLOAD_DIR, OUTPUT_DIR, MODEL_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Subtitle web UI: http://{host}:{port}")
    server.serve_forever()
    return 0


class Handler(BaseHTTPRequestHandler):
    server_version = "SubtitleWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return

        if parsed.path == "/api/jobs":
            self.send_json({"jobs": list_jobs()})
            return

        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = get_job(job_id)
            if job is None:
                self.send_error_json(404, "Job not found")
                return
            self.send_json(job)
            return

        if parsed.path == "/download":
            query = parse_qs(parsed.query)
            job_id = query.get("job", [""])[0]
            download_type = query.get("type", [""])[0]
            job = get_job(job_id)
            if job is None:
                self.send_error_json(404, "Output not found")
                return

            output_path_str = ""
            if download_type == "source":
                output_path_str = job.get("source_output", "")
            elif download_type == "translated":
                output_path_str = job.get("translated_output", "")
            else:
                # Default: prefer translated, fallback to source
                output_path_str = job.get("translated_output", "") or job.get("output", "")

            if not output_path_str:
                self.send_error_json(404, "Output not found")
                return

            output_path = Path(output_path_str).resolve()
            if not output_path.exists() or not output_path.is_relative_to(OUTPUT_DIR.resolve()):
                self.send_error_json(404, "Output not found")
                return

            self.send_file(output_path, "application/x-subrip", download_name=output_path.name)
            return

        # ── Pipeline API ──
        if parsed.path == "/api/pipeline/scan":
            self.send_json(_run_pipeline_command("scan"))
            return

        if parsed.path == "/api/pipeline/status":
            self.send_json(_run_pipeline_command("status"))
            return

        if parsed.path == "/api/pipeline/review":
            self.send_json(_run_pipeline_command("review"))
            return

        if parsed.path == "/api/pipeline/logs":
            self.send_json(_read_pipeline_log())
            return

        if parsed.path == "/api/pipeline/task":
            with PIPELINE_TASK_LOCK:
                self.send_json(dict(PIPELINE_TASK))
            return

        # ── Provider API ──
        if parsed.path == "/api/providers":
            from provider_store import list_providers
            self.send_json({"providers": list_providers(mask_secret=True)})
            return

        if parsed.path == "/api/providers/active":
            from provider_store import get_active_provider, mask_api_key
            provider = get_active_provider()
            if provider:
                provider["api_key_masked"] = mask_api_key(provider.pop("api_key", ""))
            self.send_json({"active": provider})
            return

        # ── Language Profile API ──
        if parsed.path == "/api/language-profiles":
            from language_profile_store import list_language_profiles
            self.send_json({"profiles": list_language_profiles()})
            return

        if parsed.path == "/api/language-profiles/active":
            from language_profile_store import get_active_language_profile
            self.send_json({"active": get_active_language_profile()})
            return

        self.send_error_json(404, "Not found")

    def do_PUT(self) -> None:
        """Handle PUT requests — Provider and Language Profile updates."""
        parsed = urlparse(self.path)
        path_parts = parsed.path.split("/")

        # Language Profile update
        if parsed.path.startswith("/api/language-profiles/") and len(path_parts) == 4:
            lpid = path_parts[3]
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "请求体为空")
                return
            body["id"] = lpid
            from language_profile_store import upsert_language_profile, validate_language_profile
            errors = validate_language_profile(body)
            if errors:
                self.send_error_json(400, "; ".join(errors))
                return
            try:
                result = upsert_language_profile(body)
                self.send_json({"ok": True, "profile": result})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider update
        if parsed.path.startswith("/api/providers/") and len(path_parts) == 4:
            provider_id = path_parts[3]
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "请求体为空")
                return
            body["id"] = provider_id
            from provider_store import upsert_provider, mask_api_key
            try:
                result = upsert_provider(body)
                result["api_key_masked"] = mask_api_key(result.pop("api_key", ""))
                self.send_json({"ok": True, "provider": result})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        self.send_error_json(404, "Not found")

    def do_DELETE(self) -> None:
        """Handle DELETE requests — Provider and Language Profile deletions."""
        parsed = urlparse(self.path)
        path_parts = parsed.path.split("/")

        # Language Profile delete
        if parsed.path.startswith("/api/language-profiles/") and len(path_parts) == 4:
            lpid = path_parts[3]
            from language_profile_store import delete_language_profile
            try:
                delete_language_profile(lpid)
                self.send_json({"ok": True})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider delete
        if parsed.path.startswith("/api/providers/") and len(path_parts) == 4:
            provider_id = path_parts[3]
            from provider_store import delete_provider
            try:
                delete_provider(provider_id)
                self.send_json({"ok": True})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        self.send_error_json(404, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        # ── Pipeline: run (background) — 完整处理 input 目录 ──
        if parsed.path == "/api/pipeline/run":
            body = self._read_json_body() or {}
            provider_id = body.get("provider") or body.get("provider_id", "")
            language_profile_id = body.get("language_profile") or body.get("language_profile_id", "")
            with PIPELINE_TASK_LOCK:
                if PIPELINE_TASK["running"]:
                    self.send_json(
                        {"ok": False, "error": "已有流水线任务正在运行，请等待完成"},
                        status=409,
                    )
                    return
                PIPELINE_TASK["running"] = True
                PIPELINE_TASK["action"] = "run"
                PIPELINE_TASK["started_at"] = time.time()

            thread = threading.Thread(target=_run_pipeline_background, args=("run", provider_id, language_profile_id), daemon=True)
            thread.start()
            self.send_json({"ok": True, "message": "流水线已启动，正在处理 input 目录"}, status=202)
            return

        # ── Pipeline: retry-failed (background) — 仅重试失败任务 ──
        if parsed.path == "/api/pipeline/retry-failed":
            body = self._read_json_body() or {}
            provider_id = body.get("provider") or body.get("provider_id", "")
            language_profile_id = body.get("language_profile") or body.get("language_profile_id", "")
            with PIPELINE_TASK_LOCK:
                if PIPELINE_TASK["running"]:
                    self.send_json(
                        {"ok": False, "error": "已有流水线任务正在运行，请等待完成"},
                        status=409,
                    )
                    return
                PIPELINE_TASK["running"] = True
                PIPELINE_TASK["action"] = "retry-failed"
                PIPELINE_TASK["started_at"] = time.time()

            thread = threading.Thread(target=_run_pipeline_background, args=("retry-failed", provider_id, language_profile_id), daemon=True)
            thread.start()
            self.send_json({"ok": True, "message": "retry-failed 已启动，仅重试之前失败的任务"}, status=202)
            return

        # ── Language Profile API (POST) ──
        if parsed.path == "/api/language-profiles":
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "请求体为空")
                return
            from language_profile_store import upsert_language_profile, validate_language_profile
            errors = validate_language_profile(body)
            if errors:
                self.send_error_json(400, "; ".join(errors))
                return
            try:
                result = upsert_language_profile(body)
                self.send_json({"ok": True, "profile": result}, status=201)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Language Profile activate
        if parsed.path.startswith("/api/language-profiles/") and parsed.path.endswith("/activate"):
            lpid = parsed.path.split("/")[3]
            from language_profile_store import set_active_language_profile
            try:
                set_active_language_profile(lpid)
                self.send_json({"ok": True, "active": lpid})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # ── Provider API (POST) ──
        if parsed.path == "/api/providers":
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "请求体为空")
                return
            from provider_store import upsert_provider, mask_api_key
            try:
                result = upsert_provider(body)
                result["api_key_masked"] = mask_api_key(result.pop("api_key", ""))
                self.send_json({"ok": True, "provider": result}, status=201)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider activate
        if parsed.path.startswith("/api/providers/") and parsed.path.endswith("/activate"):
            provider_id = parsed.path.split("/")[3]
            from provider_store import set_active_provider
            try:
                set_active_provider(provider_id)
                self.send_json({"ok": True, "active": provider_id})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider test
        if parsed.path.startswith("/api/providers/") and parsed.path.endswith("/test"):
            provider_id = parsed.path.split("/")[3]
            from provider_store import test_provider_connection
            result = test_provider_connection(provider_id)
            self.send_json(result)
            return

        if parsed.path != "/api/jobs":
            self.send_error_json(404, "Not found")
            return

        try:
            form = self.read_multipart_form()
            job = create_job(form)
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return
        except Exception as exc:
            self.send_error_json(500, f"Could not create job: {exc}")
            return

        thread = threading.Thread(target=run_job, args=(job["id"],), daemon=True)
        thread.start()
        # Use get_job() for response to strip _api_key
        self.send_json(get_job(job["id"]), status=201)

    def _read_json_body(self) -> dict | None:
        """读取 JSON 请求体。Content-Type 检查宽松。"""
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def read_multipart_form(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Expected multipart/form-data.")

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Empty request body.")

        raw_body = self.rfile.read(length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + raw_body
        )

        form: dict[str, str | dict] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            if not name:
                continue

            payload = part.get_payload(decode=True) or b""
            if filename:
                form[name] = {"filename": Path(filename).name, "content": payload}
            else:
                form[name] = payload.decode("utf-8", errors="replace")

        return form

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def send_file(self, path: Path, content_type: str, download_name: str | None = None) -> None:
        if not path.exists():
            self.send_error_json(404, "File not found")
            return

        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


# ── Pipeline API helpers ──────────────────────────────────────────────────


def _run_pipeline_command(action: str, timeout: int = 30) -> dict:
    """Run batch_worker.py --<action> and return structured result."""
    command = [
        sys.executable, "-B",
        str(PROJECT_ROOT / "batch_worker.py"),
        f"--{action}",
    ]
    env = os.environ.copy()
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    clear_proxy_env(env)

    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "command": action,
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": action, "error": f"命令超时（{timeout}s）"}
    except FileNotFoundError:
        return {"ok": False, "command": action, "error": f"Python 解释器未找到: {sys.executable}"}
    except Exception as exc:
        return {"ok": False, "command": action, "error": str(exc)}


def _run_pipeline_background(action: str, provider_id: str = "", language_profile_id: str = "") -> None:
    """Run batch_worker.py with --<action> in background, writing output to pipeline log.

    Args:
        action: "run" (full pipeline) or "retry-failed" (retry only)
        provider_id: optional provider ID for LLM API config
        language_profile_id: optional language profile ID for ASR/translation/quality config
    """
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)

    if action == "run":
        command = [
            sys.executable, "-B",
            str(PROJECT_ROOT / "batch_worker.py"),
            "--input", str(PROJECT_ROOT / "input"),
        ]
    else:
        command = [
            sys.executable, "-B",
            str(PROJECT_ROOT / "batch_worker.py"),
            f"--{action}",
        ]

    # Auto-detect active provider if not specified
    if not provider_id:
        try:
            from provider_store import get_active_provider
            active = get_active_provider()
            if active:
                provider_id = active.get("id", "")
        except Exception:
            pass

    # Auto-detect active language profile if not specified
    if not language_profile_id:
        try:
            from language_profile_store import get_active_language_profile
            active_lp = get_active_language_profile()
            if active_lp:
                language_profile_id = active_lp.get("id", "")
        except Exception:
            pass

    if provider_id:
        command += ["--provider", provider_id]
    if language_profile_id:
        command += ["--language-profile", language_profile_id]

    env = os.environ.copy()
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    clear_proxy_env(env)

    action_label = {"run": "完整流水线", "retry-failed": "重试失败任务"}.get(action, action)
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with PIPELINE_LOG.open("a", encoding="utf-8") as log:
        log.write(f"\n{'='*60}\n")
        log.write(f"  [{action_label}] 开始于 {started_at}\n")
        log.write(f"  命令: {' '.join(command)}\n")
        if provider_id:
            log.write(f"  Provider: {provider_id}\n")
        if language_profile_id:
            log.write(f"  Language Profile: {language_profile_id}\n")
        log.write(f"{'='*60}\n")

    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["pid"] = process.pid

        assert process.stdout is not None
        with PIPELINE_LOG.open("a", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()

        returncode = process.wait()
        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with PIPELINE_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n[{action_label}] 完成于 {finished_at}, returncode={returncode}\n")
    except Exception as exc:
        with PIPELINE_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n[{action_label}] 异常: {exc}\n")
    finally:
        with PIPELINE_TASK_LOCK:
            PIPELINE_TASK["running"] = False
            PIPELINE_TASK["pid"] = None


def _read_pipeline_log() -> dict:
    """Read pipeline log file and return its contents."""
    if not PIPELINE_LOG.exists():
        return {"ok": True, "lines": [], "text": ""}
    try:
        text = PIPELINE_LOG.read_text(encoding="utf-8")
        # Return last 200 lines max
        lines = text.splitlines()
        if len(lines) > 200:
            lines = lines[-200:]
            text = "\n".join(lines)
        return {"ok": True, "lines": lines, "text": text}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "lines": [], "text": ""}


def create_job(form: dict) -> dict:
    input_path = resolve_input(form)
    model = get_text(form, "model", "small")
    device = get_text(form, "device", "cpu")
    compute_type = get_text(form, "compute_type", "")
    language = get_text(form, "language", "")
    hf_endpoint = get_text(form, "hf_endpoint", "").strip()
    local_files_only = get_text(form, "local_files_only", "") == "on"
    beam_size = get_text(form, "beam_size", "5")
    vad = get_text(form, "vad", "on") == "on"

    if device not in {"cpu", "cuda", "auto"}:
        raise ValueError("Invalid device.")

    try:
        beam_size_int = int(beam_size)
    except ValueError as exc:
        raise ValueError("Beam size must be a number.") from exc

    if beam_size_int < 1 or beam_size_int > 10:
        raise ValueError("Beam size must be between 1 and 10.")

    # Translation options
    translate_enabled = get_text(form, "translate_enabled", "") == "on"
    api_provider = get_text(form, "api_provider", "openai-compatible")
    api_base = get_text(form, "api_base", "").strip()
    api_key = get_text(form, "api_key", "").strip()
    llm_model = get_text(form, "llm_model", "").strip()
    target_language = get_text(form, "target_language", "zh-CN").strip()
    translation_batch_size = get_text(form, "translation_batch_size", "20")
    translation_temperature = get_text(form, "translation_temperature", "0.2")
    translation_mode = get_text(form, "translation_mode", "bilingual")
    context_window = get_text(form, "context_window", "3")
    translation_prompt = get_text(form, "translation_prompt", "")

    if translate_enabled:
        if not api_base:
            raise ValueError("Translation enabled but API Base is empty.")
        if not api_key:
            raise ValueError("Translation enabled but API Key is empty.")
        if not llm_model:
            raise ValueError("Translation enabled but LLM Model is empty.")
        if api_provider not in {"openai-compatible", "anthropic"}:
            raise ValueError("Invalid API provider.")
        try:
            batch_size_int = int(translation_batch_size)
            if batch_size_int < 1 or batch_size_int > 50:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Translation batch size must be a number between 1 and 50.")
        try:
            temperature_float = float(translation_temperature)
            if temperature_float < 0 or temperature_float > 1:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Translation temperature must be a number between 0 and 1.")
        try:
            context_window_int = int(context_window)
            if context_window_int < 0 or context_window_int > 10:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Context window must be a number between 0 and 10.")

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
        "input": str(input_path),
        "output": "",
        "source_output": "",
        "translated_output": "",
        "returncode": None,
        "options": {
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "language": language,
            "hf_endpoint": hf_endpoint,
            "local_files_only": local_files_only,
            "beam_size": beam_size_int,
            "vad": vad,
            "translate_enabled": translate_enabled,
            "api_provider": api_provider,
            "api_base": api_base,
            "api_key_masked": mask_secret(api_key) if api_key else "",
            "llm_model": llm_model,
            "target_language": target_language,
            "translation_batch_size": translation_batch_size,
            "translation_temperature": translation_temperature,
            "translation_mode": translation_mode,
            "context_window": context_window,
            "translation_prompt": translation_prompt,
        },
        # Store actual api_key in memory for subprocess; never returned to frontend
        "_api_key": api_key,
        "logs": ["Queued."],
    }

    with JOBS_LOCK:
        JOBS[job_id] = job

    return job


def resolve_input(form: dict) -> Path:
    upload = form.get("file")
    path_text = get_text(form, "path", "").strip()

    if isinstance(upload, dict) and upload.get("content"):
        filename = sanitize_filename(str(upload.get("filename") or "upload.bin"))
        saved_path = UPLOAD_DIR / f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{filename}"
        saved_path.write_bytes(upload["content"])
        return saved_path.resolve()

    if path_text:
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Input path does not exist: {path}")
        return path

    raise ValueError("Choose a file or provide a local file path.")


def run_job(job_id: str) -> None:
    # Read from raw JOBS to get _api_key (get_job() strips it)
    with JOBS_LOCK:
        raw_job = JOBS.get(job_id)
    if raw_job is None:
        return

    set_job(job_id, status="running", logs=raw_job["logs"] + ["Starting transcription..."])

    options = raw_job["options"]
    command = [
        sys.executable,
        str(PROJECT_ROOT / "transcribe.py"),
        raw_job["input"],
        "--model",
        options["model"],
        "--device",
        options["device"],
        "--output-dir",
        str(OUTPUT_DIR),
        "--model-dir",
        str(MODEL_DIR),
        "--work-dir",
        str(WORK_DIR),
        "--beam-size",
        str(options["beam_size"]),
    ]

    if options["compute_type"]:
        command += ["--compute-type", options["compute_type"]]
    if options["language"]:
        command += ["--language", options["language"]]
    if options["local_files_only"]:
        command += ["--local-files-only"]
    if not options["vad"]:
        command += ["--no-vad"]

    # Translation args (API key passed via env var, NOT command line)
    if options.get("translate_enabled"):
        api_key = raw_job.get("_api_key", "")
        command += [
            "--translate",
            "--api-provider", str(options.get("api_provider", "openai-compatible")),
            "--api-base", str(options.get("api_base", "")),
            "--llm-model", str(options.get("llm_model", "")),
            "--target-language", str(options.get("target_language", "zh-CN")),
            "--translation-batch-size", str(options.get("translation_batch_size", "20")),
            "--translation-temperature", str(options.get("translation_temperature", "0.2")),
            "--translation-mode", str(options.get("translation_mode", "bilingual")),
            "--context-window", str(options.get("context_window", "3")),
        ]
        prompt = str(options.get("translation_prompt", ""))
        if prompt:
            command += ["--translation-prompt", prompt]

    env = os.environ.copy()
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    clear_proxy_env(env)
    if options["hf_endpoint"]:
        env["HF_ENDPOINT"] = options["hf_endpoint"]
    if options.get("translate_enabled"):
        env["SUBTITLE_LLM_API_KEY"] = raw_job.get("_api_key", "")

    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    logs = get_job(job_id)["logs"]
    assert process.stdout is not None
    for line in process.stdout:
        logs = append_log(job_id, line.rstrip())

    returncode = process.wait()
    source_output, translated_output = find_output_paths(raw_job)
    if returncode == 0:
        set_job(job_id, status="done", returncode=returncode,
                output=translated_output or source_output,
                source_output=source_output,
                translated_output=translated_output,
                logs=logs + ["Finished."])
    else:
        set_job(job_id, status="failed", returncode=returncode,
                output=translated_output or source_output,
                source_output=source_output,
                translated_output=translated_output,
                logs=logs + [f"Failed with code {returncode}."])

    # Clear API key from memory after job completes
    with JOBS_LOCK:
        job_record = JOBS.get(job_id)
        if job_record:
            job_record.pop("_api_key", None)


def find_output_paths(job: dict | None) -> tuple[str, str]:
    """Return (source_output, translated_output) paths."""
    if not job:
        return ("", "")
    input_path = Path(job["input"])
    model = job["options"]["model"]
    options = job["options"]

    source = OUTPUT_DIR / f"{input_path.stem}.{model}.srt"
    source_str = str(source.resolve()) if source.exists() else ""

    translated_str = ""
    if options.get("translate_enabled"):
        target = options.get("target_language", "zh-CN")
        mode_tag = "bilingual" if options.get("translation_mode", "bilingual") == "bilingual" else "translated"
        translated = OUTPUT_DIR / f"{input_path.stem}.{model}.{mode_tag}.{target}.srt"
        translated_str = str(translated.resolve()) if translated.exists() else ""

    return (source_str, translated_str)


def mask_secret(value: str) -> str:
    """Mask a secret value, showing only a prefix and suffix."""
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:3] + "..." + value[-4:]


def get_text(form: dict, key: str, default_value: str) -> str:
    value = form.get(key, default_value)
    return value if isinstance(value, str) else default_value


def sanitize_filename(name: str) -> str:
    clean = "".join(char for char in name if char not in '<>:"/\\|?*').strip()
    return clean or "upload.bin"


def get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        # Exclude internal keys from serialization
        safe = {k: v for k, v in job.items() if not k.startswith("_")}
        return json.loads(json.dumps(safe, ensure_ascii=False))


def set_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def append_log(job_id: str, line: str) -> list[str]:
    with JOBS_LOCK:
        job = JOBS[job_id]
        if line:
            job["logs"].append(line)
            job["logs"] = job["logs"][-300:]
        job["updated_at"] = time.time()
        return list(job["logs"])


def list_jobs() -> list[dict]:
    with JOBS_LOCK:
        return [
            {
                "id": job["id"],
                "status": job["status"],
                "input": job["input"],
                "output": job.get("output", ""),
                "source_output": job.get("source_output", ""),
                "translated_output": job.get("translated_output", ""),
                "options": job["options"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
            }
            for job in sorted(JOBS.values(), key=lambda item: item["created_at"], reverse=True)
        ]


def clear_proxy_env(env: dict[str, str]) -> None:
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GIT_HTTP_PROXY",
        "GIT_HTTPS_PROXY",
    ):
        env.pop(key, None)


if __name__ == "__main__":
    raise SystemExit(main())
