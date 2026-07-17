from __future__ import annotations

import json
import os
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app_info import get_app_info
from asr_evidence_api import get_asr_evidence_report, list_asr_evidence_reports
from file_inspection_api import inspect_input_file
from job_api import get_job, list_jobs, retry_job, sanitize_filename, start_job
from pipeline_api import (
    get_pipeline_task,
    pipeline_progress,
    read_pipeline_log,
    resolve_pipeline_artifact,
    run_pipeline_command,
    start_pipeline_background,
)
from provider_profile_api import (
    activate_profile_payload,
    activate_provider_payload,
    active_profile_payload,
    active_provider_payload,
    delete_profile_payload,
    delete_provider_payload,
    get_profile_payload,
    get_provider_payload,
    list_profile_payload,
    list_provider_payload,
    provider_templates_payload,
    save_profile_payload,
    save_provider_payload,
    test_provider_payload,
)
from runtime_paths import resolve_runtime_paths
from request_security import ensure_session_token, security_headers, validate_request
from subtitle_preview_api import (
    SubtitlePreviewError,
    job_subtitle_preview,
    pipeline_subtitle_preview,
)
from config_recovery import (
    ConfigCorruptError,
    ConfigRecoveryError,
    all_config_status,
    recover_store,
)


PATHS = resolve_runtime_paths()
PROJECT_ROOT = PATHS.project_root
APP_ROOT = PATHS.app_root
SRC_ROOT = PATHS.src_root
WEB_ROOT = APP_ROOT / "web"
UPLOAD_DIR = PATHS.uploads_dir
OUTPUT_DIR = PATHS.output_dir
MODEL_DIR = PATHS.models_dir
WORK_DIR = PATHS.work_dir
ASR_EVIDENCE_DIR = OUTPUT_DIR / "reports" / "asr_evidence"
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
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


class RequestBodyError(ValueError):
    """A client-safe JSON body validation failure."""


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
    ensure_session_token(server)
    print(f"Subtitle web UI: http://{host}:{port}")
    server.serve_forever()
    return 0


class Handler(BaseHTTPRequestHandler):
    server_version = "SubtitleWeb/1.0"

    def do_GET(self) -> None:
        self._log_request()
        if not self._authorize_request():
            return
        try:
            self._do_GET_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_GET_impl(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/session":
            self.send_json({"ok": True, "token": ensure_session_token(self.server)})
            return

        if parsed.path == "/api/config/status":
            self.send_json(all_config_status())
            return
        if parsed.path in ("/", "/index.html"):
            self.send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return

        if parsed.path == "/assets/brand-mark.png":
            self.send_file(WEB_ROOT / "assets" / "brand-mark.png", "image/png")
            return

        if parsed.path == "/assets/fonts/BarlowCondensed-SemiBold.ttf":
            self.send_file(
                WEB_ROOT / "assets" / "fonts" / "BarlowCondensed-SemiBold.ttf",
                "font/ttf",
            )
            return

        if parsed.path == "/assets/fonts/NotoSansSC-Variable.ttf":
            self.send_file(
                WEB_ROOT / "assets" / "fonts" / "NotoSansSC-Variable.ttf",
                "font/ttf",
            )
            return

        if parsed.path == "/api/app-info":
            self.send_json(get_app_info(PATHS))
            return

        if parsed.path == "/api/jobs":
            self.send_json({"jobs": list_jobs()})
            return

        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/preview"):
            job_id = unquote(parsed.path[len("/api/jobs/"):-len("/preview")]).strip("/")
            query = parse_qs(parsed.query)
            try:
                payload = job_subtitle_preview(
                    job_id=job_id,
                    artifact=(query.get("artifact") or [""])[0],
                    offset=(query.get("offset") or [0])[0],
                    limit=(query.get("limit") or [None])[0],
                    job=get_job(job_id),
                    output_dir=OUTPUT_DIR,
                )
            except SubtitlePreviewError as exc:
                self.send_error_json(exc.status, str(exc))
                return
            self.send_json(payload)
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

        if parsed.path == "/api/pipeline/preview":
            query = parse_qs(parsed.query)
            try:
                payload = pipeline_subtitle_preview(
                    task_id=(query.get("task") or [""])[0].strip(),
                    artifact=(query.get("artifact") or [""])[0].strip(),
                    offset=(query.get("offset") or [0])[0],
                    limit=(query.get("limit") or [None])[0],
                    resolver=resolve_pipeline_artifact,
                )
            except SubtitlePreviewError as exc:
                self.send_error_json(exc.status, str(exc))
                return
            self.send_json(payload)
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

        if parsed.path == "/api/runtime/diagnostic-bundle/download":
            from diagnostic_bundle import resolve_diagnostic_bundle

            file_name = (parse_qs(parsed.query).get("file") or [""])[0]
            bundle = resolve_diagnostic_bundle(file_name)
            if bundle is None:
                self.send_error_json(404, "Diagnostic bundle not found")
            else:
                self.send_file(bundle, "application/zip", download_name=bundle.name)
            return

        if parsed.path == "/api/storage/status":
            self.send_json(_storage_status())
            return

        if parsed.path == "/api/asr-evidence/reports":
            self.send_json(list_asr_evidence_reports(ASR_EVIDENCE_DIR, _format_bytes))
            return

        if parsed.path == "/api/asr-evidence/report":
            query = parse_qs(parsed.query)
            file_name = (query.get("file") or [""])[0].strip()
            payload, status = get_asr_evidence_report(ASR_EVIDENCE_DIR, file_name)
            self.send_json(payload, status=status)
            return

        if parsed.path == "/api/translation/effective-config":
            query = parse_qs(parsed.query)
            self.send_json(_effective_translation_config(query))
            return

        # Provider API
        if parsed.path == "/api/providers":
            self.send_json(list_provider_payload())
            return

        if parsed.path == "/api/providers/active":
            self.send_json(active_provider_payload())
            return

        path_parts = parsed.path.split("/")
        if parsed.path.startswith("/api/providers/") and len(path_parts) == 4:
            provider_id = unquote(path_parts[3])
            payload, status = get_provider_payload(provider_id)
            self.send_json(payload, status=status)
            return

        # Language Profile API
        if parsed.path == "/api/language-profiles":
            self.send_json(list_profile_payload())
            return

        if parsed.path == "/api/language-profiles/active":
            self.send_json(active_profile_payload())
            return

        path_parts = parsed.path.split("/")
        if parsed.path.startswith("/api/language-profiles/") and len(path_parts) == 4:
            profile_id = unquote(path_parts[3])
            payload, status = get_profile_payload(profile_id)
            self.send_json(payload, status=status)
            return

        # Provider Templates
        if parsed.path == "/api/provider-templates":
            self.send_json(provider_templates_payload())
            return

        self.send_error_json(404, "Not found")

    def do_PUT(self) -> None:
        self._log_request()
        if not self._authorize_request():
            return
        try:
            self._do_PUT_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_PUT_impl(self) -> None:
        parsed = urlparse(self.path)
        path_parts = parsed.path.split("/")

        # Language Profile update
        if parsed.path.startswith("/api/language-profiles/") and len(path_parts) == 4:
            lpid = unquote(path_parts[3])
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
                return
            try:
                payload, status = save_profile_payload(body, lpid)
                self.send_json(payload, status=status)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider update
        if parsed.path.startswith("/api/providers/") and len(path_parts) == 4:
            provider_id = unquote(path_parts[3])
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
                return
            try:
                payload, status = save_provider_payload(body, provider_id)
                self.send_json(payload, status=status)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        self.send_error_json(404, "Not found")

    def do_DELETE(self) -> None:
        self._log_request()
        if not self._authorize_request():
            return
        try:
            self._do_DELETE_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_DELETE_impl(self) -> None:
        parsed = urlparse(self.path)
        path_parts = parsed.path.split("/")

        # Language Profile delete
        if parsed.path.startswith("/api/language-profiles/") and len(path_parts) == 4:
            lpid = unquote(path_parts[3])
            try:
                self.send_json(delete_profile_payload(lpid))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider delete
        if parsed.path.startswith("/api/providers/") and len(path_parts) == 4:
            provider_id = unquote(path_parts[3])
            try:
                self.send_json(delete_provider_payload(provider_id))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        self.send_error_json(404, "Not found")

    def do_POST(self) -> None:
        self._log_request()
        if not self._authorize_request():
            return
        try:
            self._do_POST_impl()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_POST_impl(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/config/recover":
            body = self._read_json_body() or {}
            try:
                self.send_json(recover_store(str(body.get("store") or ""), str(body.get("action") or "")))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            except ConfigRecoveryError as exc:
                self.send_error_json(409, str(exc), code="config_recovery_failed")
            return

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
            try:
                asr_strategy = _parse_asr_strategy_payload(body, model)
                reliability = _parse_translation_reliability_payload(body)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            subtitle_formats = body.get("subtitle_formats", ["srt"])
            ass_style_id = body.get("ass_style_id", "")
            try:
                routing_payload = _parse_segment_routing_payload(body)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return

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
                asr_experiment_mode=asr_strategy["mode"],
                asr_candidate_id=asr_strategy["candidate_id"],
                **reliability,
                subtitle_formats=subtitle_formats,
                ass_style_id=ass_style_id,
                **routing_payload,
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
            try:
                asr_strategy = _parse_asr_strategy_payload(body, model)
                reliability = _parse_translation_reliability_payload(body)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            subtitle_formats = body.get("subtitle_formats", ["srt"])
            ass_style_id = body.get("ass_style_id", "")
            try:
                routing_payload = _parse_segment_routing_payload(body)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return

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
                asr_experiment_mode=asr_strategy["mode"],
                asr_candidate_id=asr_strategy["candidate_id"],
                **reliability,
                subtitle_formats=subtitle_formats,
                ass_style_id=ass_style_id,
                **routing_payload,
            )
            self.send_json(payload, status=status)
            return

        # Language Profile API (POST)
        if parsed.path == "/api/language-profiles":
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
                return
            try:
                payload, status = save_profile_payload(body)
                self.send_json(payload, status=status)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Language Profile activate
        if parsed.path.startswith("/api/language-profiles/") and parsed.path.endswith("/activate"):
            lpid = unquote(parsed.path.split("/")[3])
            try:
                self.send_json(activate_profile_payload(lpid))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider API (POST)
        if parsed.path == "/api/providers":
            body = self._read_json_body()
            if not body:
                self.send_error_json(400, "Request body is empty.")
                return
            try:
                payload, status = save_provider_payload(body)
                self.send_json(payload, status=status)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider activate
        if parsed.path.startswith("/api/providers/") and parsed.path.endswith("/activate"):
            provider_id = unquote(parsed.path.split("/")[3])
            try:
                self.send_json(activate_provider_payload(provider_id))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            return

        # Provider test
        if parsed.path.startswith("/api/providers/") and parsed.path.endswith("/test"):
            provider_id = unquote(parsed.path.split("/")[3])
            self.send_json(test_provider_payload(provider_id))
            return

        if parsed.path == "/api/storage/cleanup":
            self.send_json(_cleanup_transient_files())
            return

        if parsed.path == "/api/files/inspect":
            body = self._read_json_body() or {}
            self.send_json(
                inspect_input_file(
                    body, output_dir=OUTPUT_DIR, supported_extensions=SUPPORTED_MEDIA_EXTENSIONS
                )
            )
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

        if parsed.path == "/api/runtime/diagnostic-bundle":
            from diagnostic_bundle import DiagnosticBundleBusy
            from runtime_api import create_runtime_diagnostic_bundle

            try:
                self.send_json(create_runtime_diagnostic_bundle(), status=201)
            except DiagnosticBundleBusy as exc:
                self.send_error_json(409, str(exc))
            return

        if parsed.path != "/api/jobs":
            # Check for /api/jobs/<job_id>/retry before 404
            retry_match = None
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/retry"):
                retry_match = parsed.path[len("/api/jobs/"):][:-len("/retry")]
            if retry_match:
                job_id = retry_match
                safe_job = get_job(job_id)
                if safe_job is None:
                    self.send_error_json(404, "Job not found")
                    return
                if safe_job.get("status") != "failed":
                    self.send_error_json(400, "只能重试失败的任务")
                    return
                if not safe_job.get("can_retry"):
                    self.send_error_json(400, safe_job.get("retry_reason") or "无法重试此任务")
                    return
                new_job = retry_job(job_id)
                if new_job is None:
                    self.send_error_json(500, "重试失败：无法创建新任务")
                    return
                self.send_json({"ok": True, "job": new_job}, status=201)
                return

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

    def _authorize_request(self) -> bool:
        failure = validate_request(self)
        if failure is None:
            return True
        status, code, message = failure
        self.send_error_json(status, message, code=code)
        return False

    def _handle_exception(self, exc: Exception) -> None:
        import traceback

        if isinstance(exc, ConfigCorruptError):
            self.send_error_json(
                409,
                "The local configuration is corrupt. Review /api/config/status before recovery.",
                code="config_corrupt",
            )
            return

        if isinstance(exc, RequestBodyError):
            self.send_error_json(400, str(exc), code="invalid_json")
            return

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
        """Read an application/json request body after request validation."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestBodyError("Invalid Content-Length.") from exc
        if length <= 0:
            return None
        if length > MAX_JSON_BYTES:
            raise RequestBodyError("JSON request body is too large.")
        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise RequestBodyError("JSON request body must be an object.")
            print(
                "[web] payload "
                + json.dumps(self._redact_payload(body), ensure_ascii=False),
                flush=True,
            )
            return body
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"[web] invalid JSON payload: {exc}", flush=True)
            raise RequestBodyError("Malformed JSON request body.") from exc

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
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self._send_security_headers()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # A local browser can close a tab or cancel polling while a response
            # is being written. Treat that as a completed request lifecycle, not
            # as an application failure that triggers a second response attempt.
            return

    def send_error_json(self, status: int, message: str, code: str | None = None) -> None:
        payload = {"ok": False, "error": message}
        if code:
            payload["code"] = code
        self.send_json(payload, status=status)

    def _send_security_headers(self) -> None:
        for name, value in security_headers():
            self.send_header(name, value)

    def send_file(self, path: Path, content_type: str, download_name: str | None = None) -> None:
        if not path.exists():
            self.send_error_json(404, "File not found")
            return

        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self._send_security_headers()
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


def _parse_segment_routing_payload(body: dict) -> dict:
    from segment_asr_routing_integration import (
        DEFAULT_APPLY_WINDOW_SECONDS,
        DEFAULT_MAX_APPLY_WINDOWS,
        SegmentAsrRoutingError,
        SegmentAsrRoutingOptions,
        validate_options,
    )

    mode = str(body.get("segment_asr_routing") or "off").strip() or "off"
    threshold = body.get("segment_routing_confidence_threshold", 0.70)
    min_segments = body.get("segment_routing_min_segments", 1)
    strict = _truthy(body.get("segment_routing_strict", False))
    window_seconds = body.get("segment_routing_window_seconds", DEFAULT_APPLY_WINDOW_SECONDS)
    max_windows = body.get("segment_routing_max_windows", DEFAULT_MAX_APPLY_WINDOWS)
    allow_large_run = _truthy(body.get("segment_routing_allow_large_run", False))
    try:
        options = validate_options(
            SegmentAsrRoutingOptions(
                mode=mode,
                confidence_threshold=float(threshold),
                min_segments=int(min_segments),
                strict=strict,
                window_seconds=float(window_seconds),
                max_windows=int(max_windows),
                allow_large_run=allow_large_run,
            )
        )
    except (TypeError, ValueError, SegmentAsrRoutingError) as exc:
        raise ValueError(f"Invalid segment ASR routing settings: {exc}") from exc
    return {
        "segment_asr_routing": options.mode,
        "segment_routing_confidence_threshold": options.confidence_threshold,
        "segment_routing_min_segments": options.min_segments,
        "segment_routing_strict": options.strict,
        "segment_routing_window_seconds": options.window_seconds,
        "segment_routing_max_windows": options.max_windows,
        "segment_routing_allow_large_run": options.allow_large_run,
    }


def _parse_asr_strategy_payload(body: dict, model: str) -> dict[str, str]:
    from asr_strategy import validate_strategy_config

    return validate_strategy_config(
        {
            "mode": body.get("asr_experiment_mode", "off"),
            "candidate_id": body.get("asr_candidate_id", ""),
        },
        model=str(model or "large-v3"),
    )


def _parse_translation_reliability_payload(body: dict) -> dict:
    if (
        "translation_reliability_mode" not in body
        and "translation_max_extra_requests" not in body
    ):
        return {}
    from translation_reliability import normalize_reliability_config

    normalized = normalize_reliability_config(
        body.get("translation_reliability_mode", "off"),
        max_extra_requests=body.get("translation_max_extra_requests", 12),
    )
    return {
        "translation_reliability_mode": normalized["mode"],
        "translation_max_extra_requests": normalized["max_extra_requests"],
    }


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _effective_translation_config(query: dict | None = None) -> dict:
    """Resolve selected translation config without writing any local state."""
    from provider_profile_api import effective_translation_config

    return effective_translation_config(query)


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


def _asr_evidence_reports() -> dict:
    """Compatibility wrapper for callers predating the API module split."""
    return list_asr_evidence_reports(ASR_EVIDENCE_DIR, _format_bytes)


def _asr_evidence_report(file_name: str) -> tuple[dict, int]:
    """Compatibility wrapper for callers predating the API module split."""
    return get_asr_evidence_report(ASR_EVIDENCE_DIR, file_name)


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
    from storage_api import storage_status

    return storage_status(project_root=PROJECT_ROOT, work_dir=WORK_DIR, upload_dir=UPLOAD_DIR)


def _cleanup_transient_files() -> dict:
    """Delete only safe, reproducible intermediates inside the project."""
    from storage_api import cleanup_transient_files

    return cleanup_transient_files(
        project_root=PROJECT_ROOT, work_dir=WORK_DIR, upload_dir=UPLOAD_DIR
    )


if __name__ == "__main__":
    raise SystemExit(main())
