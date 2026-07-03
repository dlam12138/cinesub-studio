from __future__ import annotations

import json
import os
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from job_api import get_job, list_jobs, sanitize_filename, start_job
from pipeline_api import (
    get_pipeline_task,
    pipeline_progress,
    read_pipeline_log,
    resolve_pipeline_artifact,
    run_pipeline_command,
    start_pipeline_background,
)
from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths()
PROJECT_ROOT = PATHS.project_root
APP_ROOT = PATHS.app_root
SRC_ROOT = PATHS.src_root
WEB_ROOT = APP_ROOT / "web"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "output"
MODEL_DIR = PROJECT_ROOT / "models"
WORK_DIR = PROJECT_ROOT / "work"
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
SUPPORTED_MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
}

def redact_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:3] + "***" + value[-4:]


def _is_secret_like_key(key: object) -> bool:
    lowered = str(key).lower()
    markers = (
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "client_secret",
        "authorization",
        "password",
        "bearer",
    )
    return any(marker in lowered for marker in markers)


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
        self._log_request()
        try:
            self._do_GET_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_GET_impl(self) -> None:
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
            elif download_type == "quality_report":
                output_path_str = job.get("quality_report", "")
            elif download_type == "review_needed":
                output_path_str = job.get("review_needed", "")
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

        # Pipeline API
        if parsed.path == "/api/pipeline/scan":
            query = parse_qs(parsed.query)
            input_dir = (query.get("input_dir") or [""])[0].strip()
            self.send_json(run_pipeline_command("scan", input_dir=input_dir))
            return

        if parsed.path == "/api/pipeline/status":
            query = parse_qs(parsed.query)
            input_dir = (query.get("input_dir") or [""])[0].strip()
            self.send_json(run_pipeline_command("status", input_dir=input_dir))
            return

        if parsed.path == "/api/pipeline/progress":
            self.send_json(pipeline_progress())
            return

        if parsed.path == "/api/pipeline/artifact":
            query = parse_qs(parsed.query)
            task_id = (query.get("task") or [""])[0].strip()
            artifact_type = (query.get("artifact") or [""])[0].strip()
            artifact_path, error = resolve_pipeline_artifact(task_id, artifact_type)
            if artifact_path is None:
                self.send_error_json(404, error or "Artifact not found")
                return
            self.send_file(
                artifact_path,
                _content_type_for_artifact(artifact_path),
                download_name=artifact_path.name,
            )
            return

        if parsed.path == "/api/pipeline/review":
            query = parse_qs(parsed.query)
            input_dir = (query.get("input_dir") or [""])[0].strip()
            self.send_json(run_pipeline_command("review", input_dir=input_dir))
            return

        if parsed.path == "/api/pipeline/logs":
            self.send_json(read_pipeline_log())
            return

        if parsed.path == "/api/pipeline/task":
            self.send_json(get_pipeline_task())
            return

        if parsed.path == "/api/runtime/diagnostics":
            self.send_json(_runtime_diagnostics())
            return

        if parsed.path == "/api/runtime/download-plan":
            query = parse_qs(parsed.query)
            components = query.get("component", [])
            self.send_json(_runtime_download_plan(components))
            return

        if parsed.path == "/api/storage/status":
            self.send_json(_storage_status())
            return

        if parsed.path == "/api/translation/effective-config":
            query = parse_qs(parsed.query)
            self.send_json(_effective_translation_config(query))
            return

        # Provider API
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

        # Language Profile API
        if parsed.path == "/api/language-profiles":
            from language_profile_store import list_language_profiles
            self.send_json({"profiles": list_language_profiles()})
            return

        if parsed.path == "/api/language-profiles/active":
            from language_profile_store import get_active_language_profile
            self.send_json({"active": get_active_language_profile()})
            return

        # Provider Templates
        if parsed.path == "/api/provider-templates":
            from provider_store import get_provider_templates
            self.send_json({"ok": True, "templates": get_provider_templates()})
            return

        self.send_error_json(404, "Not found")

    def do_PUT(self) -> None:
        self._log_request()
        try:
            self._do_PUT_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_PUT_impl(self) -> None:
        parsed = urlparse(self.path)
        path_parts = parsed.path.split("/")

        # Language Profile update
        if parsed.path.startswith("/api/language-profiles/") and len(path_parts) == 4:
            lpid = path_parts[3]
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
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
                self.send_error_json(400, "Request body is empty.")
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
        self._log_request()
        try:
            self._do_DELETE_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_DELETE_impl(self) -> None:
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
        self._log_request()
        try:
            self._do_POST_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_POST_impl(self) -> None:
        parsed = urlparse(self.path)

        # Pipeline: run in the background for the input directory.
        if parsed.path == "/api/pipeline/run":
            body = self._read_json_body() or {}
            provider_id = body.get("provider") or body.get("provider_id", "")
            language_profile_id = body.get("language_profile") or body.get("language_profile_id", "")
            input_dir = body.get("input_dir", "").strip()
            model = body.get("model", "small")
            device = body.get("device", "auto")
            compute_type = body.get("compute_type", "")
            translate_enabled = body.get("translate_enabled", True)
            language = body.get("language", "")
            hf_endpoint = body.get("hf_endpoint", "").strip()
            local_files_only = bool(body.get("local_files_only", False))
            subtitle_formats = body.get("subtitle_formats", ["srt"])
            ass_style_id = body.get("ass_style_id", "")

            payload, status = start_pipeline_background(
                action="run",
                provider_id=provider_id,
                language_profile_id=language_profile_id,
                input_dir=input_dir,
                model=model,
                device=device,
                compute_type=compute_type,
                translate_enabled=translate_enabled,
                language=language,
                hf_endpoint=hf_endpoint,
                local_files_only=local_files_only,
                subtitle_formats=subtitle_formats,
                ass_style_id=ass_style_id,
            )
            self.send_json(payload, status=status)
            return

        # Pipeline: retry failed tasks in the background.
        if parsed.path == "/api/pipeline/retry-failed":
            body = self._read_json_body() or {}
            provider_id = body.get("provider") or body.get("provider_id", "")
            language_profile_id = body.get("language_profile") or body.get("language_profile_id", "")
            input_dir = body.get("input_dir", "").strip()
            model = body.get("model", "small")
            device = body.get("device", "auto")
            compute_type = body.get("compute_type", "")
            translate_enabled = body.get("translate_enabled", True)
            language = body.get("language", "")
            hf_endpoint = body.get("hf_endpoint", "").strip()
            local_files_only = bool(body.get("local_files_only", False))
            subtitle_formats = body.get("subtitle_formats", ["srt"])
            ass_style_id = body.get("ass_style_id", "")

            payload, status = start_pipeline_background(
                action="retry-failed",
                provider_id=provider_id,
                language_profile_id=language_profile_id,
                input_dir=input_dir,
                model=model,
                device=device,
                compute_type=compute_type,
                translate_enabled=translate_enabled,
                language=language,
                hf_endpoint=hf_endpoint,
                local_files_only=local_files_only,
                subtitle_formats=subtitle_formats,
                ass_style_id=ass_style_id,
            )
            self.send_json(payload, status=status)
            return

        # Language Profile API (POST)
        if parsed.path == "/api/language-profiles":
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
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

        # Provider API (POST)
        if parsed.path == "/api/providers":
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
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

        if parsed.path == "/api/storage/cleanup":
            self.send_json(_cleanup_transient_files())
            return

        if parsed.path == "/api/files/inspect":
            body = self._read_json_body() or {}
            self.send_json(_inspect_input_file(body))
            return

        if parsed.path == "/api/runtime/download":
            body = self._read_json_body() or {}
            components = body.get("components") or []
            if isinstance(components, str):
                components = [components]
            dry_run = body.get("dry_run", True) is not False
            self.send_json(_runtime_download(components, dry_run=dry_run))
            return

        if parsed.path == "/api/runtime/import-package":
            try:
                if "multipart/form-data" in self.headers.get("Content-Type", ""):
                    form = self.read_multipart_form()
                    self.send_json(_runtime_import_uploaded_package(form))
                else:
                    body = self._read_json_body() or {}
                    self.send_json(_runtime_import_package(body))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        if parsed.path != "/api/jobs":
            self.send_error_json(404, "Not found")
            return

        try:
            form = self.read_multipart_form()
            job = start_job(form)
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return
        except Exception as exc:
            self.send_error_json(500, f"Could not create job: {exc}")
            return

        self.send_json(job, status=201)

    def _log_request(self) -> None:
        print(f"[web] {self.command} {self.path}", flush=True)

    def _handle_exception(self, exc: Exception) -> None:
        import traceback

        traceback.print_exc()
        try:
            self.send_error_json(500, f"Server error: {exc}")
        except Exception:
            pass

    def _redact_payload(self, obj):
        if isinstance(obj, dict):
            redacted = {}
            for key, value in obj.items():
                lowered = str(key).lower()
                if _is_secret_like_key(lowered):
                    redacted[key] = redact_secret(str(value or ""))
                else:
                    redacted[key] = self._redact_payload(value)
            return redacted
        if isinstance(obj, list):
            return [self._redact_payload(item) for item in obj]
        return obj

    def _read_json_body(self) -> dict | None:
        """Read a JSON request body with a loose Content-Type check."""
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return None
        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
            print(
                "[web] payload "
                + json.dumps(self._redact_payload(body), ensure_ascii=False),
                flush=True,
            )
            return body
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"[web] invalid JSON payload: {exc}", flush=True)
            return None

    def read_multipart_form(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Expected multipart/form-data.")

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Empty request body.")
        if length > MAX_UPLOAD_BYTES:
            limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            raise ValueError(
                f"Uploaded file is too large for the browser upload path ({limit_mb}MB limit). "
                "For full movies, enter the local target file path so the server reads it directly."
            )

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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status=status)

    def send_file(self, path: Path, content_type: str, download_name: str | None = None) -> None:
        if not path.exists():
            self.send_error_json(404, "File not found")
            return

        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if path.resolve() == (WEB_ROOT / "index.html").resolve():
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def _runtime_diagnostics() -> dict:
    """Return environment diagnostics for the local web process."""
    from runtime_api import get_runtime_diagnostics

    return get_runtime_diagnostics()


def _first_query_value(query: dict, key: str) -> str:
    values = query.get(key) or [""]
    return str(values[0] or "").strip()


def _effective_translation_config(query: dict | None = None) -> dict:
    """Resolve selected translation config without writing any local state."""
    query = query or {}
    provider_id = _first_query_value(query, "provider_id") or _first_query_value(query, "provider")
    profile_id = _first_query_value(query, "language_profile_id") or _first_query_value(query, "language_profile")
    warnings: list[str] = []

    provider_summary = {
        "id": "",
        "name": "",
        "protocol": "",
        "model": "",
        "api_key_present": False,
        "api_key_masked": "",
        "status": "not_configured",
    }
    try:
        from provider_store import get_active_provider, get_provider, mask_api_key

        provider = get_provider(provider_id) if provider_id else get_active_provider()
        if provider:
            api_key = str(provider.get("api_key") or "")
            provider_summary = {
                "id": str(provider.get("id") or ""),
                "name": str(provider.get("name") or provider.get("id") or ""),
                "protocol": str(provider.get("protocol") or "openai-compatible"),
                "model": str(provider.get("translation_model") or ""),
                "api_key_present": bool(api_key),
                "api_key_masked": mask_api_key(api_key) if api_key else "",
                "status": "ok" if provider.get("enabled", True) else "disabled",
            }
            if not provider_summary["model"]:
                warnings.append("Selected Provider has no translation model.")
            if not api_key:
                warnings.append("Selected Provider has no API key.")
        else:
            warnings.append("No Provider is configured for translation.")
    except Exception as exc:
        provider_summary["status"] = "error"
        warnings.append(f"Provider preview failed: {exc}")

    profile_summary = {
        "id": "",
        "name": "",
        "type": "default",
        "source_language": "auto",
        "target_language": "zh-CN",
        "style_present": False,
        "glossary_count": 0,
        "quality": {},
        "quality_source": "default",
        "status": "not_configured",
    }
    try:
        from language_profile_store import (
            get_active_language_profile,
            get_language_profile,
            list_language_profiles,
            normalize_glossary,
        )

        profile = get_language_profile(profile_id) if profile_id else get_active_language_profile()
        listed = {p.get("id"): p for p in list_language_profiles()}
        if profile:
            listed_profile = listed.get(profile.get("id"), profile)
            glossary = normalize_glossary(profile.get("glossary", []))
            is_builtin = bool(listed_profile.get("builtin", False))
            profile_summary = {
                "id": str(profile.get("id") or ""),
                "name": str(profile.get("name") or profile.get("id") or ""),
                "type": "builtin" if is_builtin else "local",
                "source_language": str(profile.get("source_language") or "auto"),
                "target_language": str(profile.get("target_language") or "zh-CN"),
                "style_present": bool(str(profile.get("translation_style") or "").strip()),
                "glossary_count": len(glossary),
                "quality": profile.get("quality", {}) if isinstance(profile.get("quality"), dict) else {},
                "quality_source": "builtin" if is_builtin else "local",
                "status": "ok",
            }
        else:
            warnings.append("No Language Profile is configured.")
    except Exception as exc:
        profile_summary["status"] = "error"
        warnings.append(f"Language Profile preview failed: {exc}")

    return {
        "ok": True,
        "provider": provider_summary,
        "language_profile": profile_summary,
        "prompt_behavior": {
            "custom_prompt_overrides_profile_style": True,
            "glossary_always_appended": True,
        },
        "cache_behavior": {
            "key_includes_effective_prompt": True,
            "note": "Translation cache entries vary by effective prompt; style or glossary changes create separate cache entries.",
        },
        "warnings": warnings,
    }


def _runtime_download_plan(components: list[str] | None = None) -> dict:
    from runtime_api import get_runtime_download_plan

    return get_runtime_download_plan(components or None)


def _runtime_download(components: list[str], dry_run: bool = True) -> dict:
    from runtime_api import run_runtime_download

    return run_runtime_download(components or [], dry_run=dry_run)


def _runtime_import_package(body: dict) -> dict:
    from runtime_api import import_runtime_package

    return import_runtime_package(body)


def _runtime_import_uploaded_package(form: dict) -> dict:
    from runtime_api import import_uploaded_runtime_package

    return import_uploaded_runtime_package(
        form=form,
        project_root=PROJECT_ROOT,
        sanitize_filename=sanitize_filename,
    )


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _content_type_for_artifact(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "application/json; charset=utf-8"
    return "application/x-subrip; charset=utf-8"


def _sum_files(paths: list[Path]) -> tuple[int, int]:
    total = 0
    count = 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
                count += 1
        except OSError:
            continue
    return count, total


def _storage_status() -> dict:
    """Return lightweight project storage info without scanning model caches."""
    audio_count, audio_size = _sum_files(list(WORK_DIR.glob("*.16k.wav")))
    upload_count, upload_size = _sum_files([p for p in UPLOAD_DIR.iterdir()] if UPLOAD_DIR.exists() else [])
    translation_cache = WORK_DIR / "translation-cache"
    cache_files = list(translation_cache.glob("*.json")) if translation_cache.exists() else []
    cache_count, cache_size = _sum_files(cache_files)
    return {
        "ok": True,
        "work_audio": {
            "path": str(WORK_DIR),
            "count": audio_count,
            "bytes": audio_size,
            "display": _format_bytes(audio_size),
        },
        "uploads": {
            "path": str(UPLOAD_DIR),
            "count": upload_count,
            "bytes": upload_size,
            "display": _format_bytes(upload_size),
        },
        "translation_cache": {
            "path": str(translation_cache),
            "count": cache_count,
            "bytes": cache_size,
            "display": _format_bytes(cache_size),
            "managed_by_cleanup": False,
        },
        "model_cache": {
            "path": str(PROJECT_ROOT / ".cache" / "huggingface"),
            "managed_by_cleanup": False,
        },
        "note": "Stopping the web service does not automatically delete caches.",
    }


def _cleanup_transient_files() -> dict:
    """Delete only safe, reproducible intermediates inside the project."""
    deleted: list[str] = []
    errors: list[str] = []
    targets: list[Path] = []
    if WORK_DIR.exists():
        targets.extend(WORK_DIR.glob("*.16k.wav"))
    if UPLOAD_DIR.exists():
        targets.extend(path for path in UPLOAD_DIR.iterdir() if path.is_file())

    allowed_roots = [WORK_DIR.resolve(), UPLOAD_DIR.resolve()]
    for target in targets:
        try:
            resolved = target.resolve()
            if not any(resolved.is_relative_to(root) for root in allowed_roots):
                errors.append(f"Skipped unexpected path: {target}")
                continue
            size = resolved.stat().st_size
            resolved.unlink()
            deleted.append(f"{target.name} ({_format_bytes(size)})")
        except OSError as exc:
            errors.append(f"{target}: {exc}")

    status = _storage_status()
    status.update({
        "ok": not errors,
        "deleted": deleted,
        "errors": errors,
    })
    return status


def _inspect_input_file(body: dict) -> dict:
    """Inspect a local media path without reading, copying, or creating files."""
    from subtitle_model import DEFAULT_ASS_STYLE_ID, normalize_subtitle_formats, plan_subtitle_outputs

    path_text = str(body.get("path") or "").strip()
    model = str(body.get("model") or "small").strip() or "small"
    target_language = str(body.get("target_language") or "zh-CN").strip() or "zh-CN"
    translation_mode = str(body.get("translation_mode") or "bilingual").strip()
    subtitle_formats = normalize_subtitle_formats(body.get("subtitle_formats") or ["srt"])
    ass_style_id = str(body.get("ass_style_id") or DEFAULT_ASS_STYLE_ID).strip() or DEFAULT_ASS_STYLE_ID
    mode_tag = "translated" if translation_mode == "translated" else "bilingual"

    if not path_text:
        return {
            "ok": False,
            "error": "Enter a local file path.",
            "path": "",
            "exists": False,
            "supported": False,
        }

    try:
        input_path = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return {
            "ok": False,
            "error": f"路径无法解析: {exc}",
            "path": path_text,
            "exists": False,
            "supported": False,
        }

    suffix = input_path.suffix.lower()
    exists = input_path.exists()
    is_file = input_path.is_file() if exists else False
    supported = suffix in SUPPORTED_MEDIA_EXTENSIONS
    readable = bool(exists and is_file and os.access(input_path, os.R_OK))
    size = 0
    mtime = None
    if exists and is_file:
        try:
            stat = input_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            readable = False

    source_output = OUTPUT_DIR / f"{input_path.stem}.{model}.srt"
    translated_output = OUTPUT_DIR / f"{input_path.stem}.{model}.{mode_tag}.{target_language}.srt"
    output_plan = plan_subtitle_outputs(
        output_root=OUTPUT_DIR,
        stem=input_path.stem,
        model=model,
        target_language=target_language,
        translation_mode=mode_tag,
        formats=subtitle_formats,
        ass_style_id=ass_style_id,
    )
    warnings: list[str] = []
    if not exists:
        warnings.append("File does not exist. Check that the path is complete.")
    elif not is_file:
        warnings.append("This path is not a file. Single-file processing needs a video or audio file.")
    if exists and is_file and not supported:
        warnings.append(f"Extension {suffix or '(none)'} is not supported.")
    if exists and is_file and not readable:
        warnings.append("The current web process may not have permission to read this file.")
    if source_output.exists():
        warnings.append("The expected source SRT already exists and may be overwritten.")
    if translated_output.exists():
        warnings.append("The expected translated SRT already exists and may be overwritten.")

    return {
        "ok": True,
        "path": str(input_path),
        "exists": exists,
        "is_file": is_file,
        "supported": supported,
        "readable": readable,
        "extension": suffix,
        "bytes": size,
        "display_size": _format_bytes(size),
        "modified_at": mtime,
        "model": model,
        "target_language": target_language,
        "translation_mode": mode_tag,
        "source_output": str(source_output.resolve()),
        "source_output_exists": source_output.exists(),
        "translated_output": str(translated_output.resolve()),
        "translated_output_exists": translated_output.exists(),
        "subtitle_output_plan": output_plan,
        "warnings": warnings,
        "ready": bool(exists and is_file and supported and readable),
    }



if __name__ == "__main__":
    raise SystemExit(main())
