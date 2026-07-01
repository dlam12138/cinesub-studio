from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from runtime_env import add_project_cuda_to_env


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = PROJECT_ROOT / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "output"
MODEL_DIR = PROJECT_ROOT / "models"
WORK_DIR = PROJECT_ROOT / "work"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def start_job(form: dict) -> dict:
    job = create_job(form)
    thread = threading.Thread(target=run_job, args=(job["id"],), daemon=True)
    thread.start()
    safe_job = get_job(job["id"])
    return safe_job if safe_job is not None else job


def create_job(form: dict) -> dict:
    from subtitle_model import DEFAULT_ASS_STYLE_ID, normalize_subtitle_formats, subtitle_format_status

    input_path = resolve_input(form)
    model = get_text(form, "model", "small")
    device = get_text(form, "device", "auto")
    compute_type = get_text(form, "compute_type", "")
    language = get_text(form, "language", "")
    hf_endpoint = get_text(form, "hf_endpoint", "").strip()
    local_files_only = get_text(form, "local_files_only", "") == "on"
    beam_size = get_text(form, "beam_size", "5")
    vad = get_text(form, "vad", "on") == "on"
    condition_on_previous_text = get_text(form, "condition_on_previous_text", "on") == "on"

    if device not in {"cpu", "cuda", "auto"}:
        raise ValueError("Invalid device.")

    try:
        beam_size_int = int(beam_size)
    except ValueError as exc:
        raise ValueError("Beam size must be a number.") from exc

    if beam_size_int < 1 or beam_size_int > 10:
        raise ValueError("Beam size must be between 1 and 10.")

    translate_enabled = get_text(form, "translate_enabled", "") == "on"
    provider_select = get_text(form, "provider_select", "").strip()
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
    requested_formats = get_text(form, "subtitle_formats", "")
    if get_text(form, "format_ass", "") == "on":
        requested_formats = "srt,ass"
    subtitle_formats = normalize_subtitle_formats(requested_formats)
    ass_style_id = get_text(form, "ass_style_id", DEFAULT_ASS_STYLE_ID).strip() or DEFAULT_ASS_STYLE_ID
    subtitle_status = subtitle_format_status(subtitle_formats, ass_style_id)

    if translate_enabled:
        if provider_select:
            try:
                from provider_store import resolve_provider_config

                provider_config = resolve_provider_config(provider_select)
            except Exception as exc:
                raise ValueError(f"Provider config error: {exc}") from exc
            api_provider = api_provider or provider_config.get("api_provider", "openai-compatible")
            api_base = api_base or provider_config.get("api_base", "")
            api_key = api_key or provider_config.get("api_key", "")
            llm_model = llm_model or provider_config.get("llm_model", "")
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

    language_profile = get_text(form, "language_profile", "").strip()
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
            "condition_on_previous_text": condition_on_previous_text,
            "translate_enabled": translate_enabled,
            "provider_id": provider_select,
            "api_provider": api_provider,
            "api_base": api_base,
            "api_key_masked": mask_secret(api_key) if api_key else "",
            "llm_model": llm_model,
            "target_language": target_language,
            "language_profile": language_profile,
            "translation_batch_size": translation_batch_size,
            "translation_temperature": translation_temperature,
            "translation_mode": translation_mode,
            "context_window": context_window,
            "translation_prompt": translation_prompt,
            "subtitle_formats": subtitle_formats,
            "ass_style_id": ass_style_id,
            "subtitle_status": subtitle_status,
        },
        "_api_key": api_key,
        "logs": ["Queued."],
    }

    with JOBS_LOCK:
        JOBS[job_id] = job

    return job


def resolve_input(form: dict) -> Path:
    upload = form.get("file")
    path_text = get_text(form, "path", "").strip()

    if path_text:
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Input path does not exist: {path}")
        return path

    if isinstance(upload, dict) and upload.get("content"):
        filename = sanitize_filename(str(upload.get("filename") or "upload.bin"))
        saved_path = UPLOAD_DIR / f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{filename}"
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_bytes(upload["content"])
        return saved_path.resolve()

    raise ValueError("Enter a local target file path, or upload a small sample file.")


def run_job(job_id: str) -> None:
    with JOBS_LOCK:
        raw_job = JOBS.get(job_id)
    if raw_job is None:
        return

    set_job(job_id, status="running", logs=raw_job["logs"] + ["Starting transcription..."])

    options = raw_job["options"]
    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "core" / "transcribe.py"),
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
    if not options.get("condition_on_previous_text", True):
        command += ["--no-condition-on-previous-text"]
    command += ["--subtitle-formats", ",".join(options.get("subtitle_formats", ["srt"]))]
    command += ["--ass-style-id", str(options.get("ass_style_id", "clean-cn"))]

    lang_profile = options.get("language_profile", "")
    if lang_profile:
        command += ["--language-profile", lang_profile]

    if options.get("translate_enabled"):
        command += [
            "--translate",
            "--api-provider",
            str(options.get("api_provider", "openai-compatible")),
            "--api-base",
            str(options.get("api_base", "")),
            "--llm-model",
            str(options.get("llm_model", "")),
            "--target-language",
            str(options.get("target_language", "zh-CN")),
            "--translation-batch-size",
            str(options.get("translation_batch_size", "20")),
            "--translation-temperature",
            str(options.get("translation_temperature", "0.2")),
            "--translation-mode",
            str(options.get("translation_mode", "bilingual")),
            "--context-window",
            str(options.get("context_window", "3")),
        ]
        prompt = str(options.get("translation_prompt", ""))
        if prompt:
            command += ["--translation-prompt", prompt]

    env = _job_env()
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
    quality_report = ""
    review_needed = ""
    if returncode == 0:
        try:
            from language_profile_store import get_active_language_profile, get_language_profile
            from quality_checker import run_quality_check

            report_dir = OUTPUT_DIR / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            source_path = Path(source_output) if source_output else None
            translated_path = Path(translated_output) if translated_output else None
            if source_path and source_path.exists():
                profile_id = options.get("language_profile", "")
                if not profile_id:
                    active = get_active_language_profile()
                    if active:
                        profile_id = active.get("id", "")
                thresholds = {}
                if profile_id:
                    profile = get_language_profile(profile_id)
                    if profile:
                        thresholds = profile.get("quality", {})
                _ = run_quality_check(
                    source_srt=source_path,
                    translated_srt=translated_path if translated_path and translated_path.exists() else None,
                    target_language=options.get("target_language", "zh-CN"),
                    output_dir=report_dir,
                    quality_thresholds=thresholds,
                )
                quality_report = str(report_dir / f"{source_path.stem}.quality_report.json")
                review_needed = str(report_dir / f"{source_path.stem}.review_needed.srt")
                logs = append_log(job_id, "Quality check completed.")
        except Exception as exc:
            logs = append_log(job_id, f"Quality check failed: {exc}")
        if "ass" in options.get("subtitle_formats", []):
            from subtitle_model import ASS_RESERVED_MESSAGE

            logs = append_log(job_id, f"ASS: {ASS_RESERVED_MESSAGE}")

        set_job(
            job_id,
            status="done",
            returncode=returncode,
            output=translated_output or source_output,
            source_output=source_output,
            translated_output=translated_output,
            quality_report=quality_report,
            review_needed=review_needed,
            logs=logs + ["Finished."],
        )
    else:
        set_job(
            job_id,
            status="failed",
            returncode=returncode,
            output=translated_output or source_output,
            source_output=source_output,
            translated_output=translated_output,
            logs=logs + [f"Failed with code {returncode}."],
        )

    with JOBS_LOCK:
        job_record = JOBS.get(job_id)
        if job_record:
            job_record.pop("_api_key", None)


def find_output_paths(job: dict | None) -> tuple[str, str]:
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


def get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
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
            job["logs"].append(clean_log_line(line))
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
                "quality_report": job.get("quality_report", ""),
                "review_needed": job.get("review_needed", ""),
                "options": job["options"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
            }
            for job in sorted(JOBS.values(), key=lambda item: item["created_at"], reverse=True)
        ]


def get_text(form: dict, key: str, default_value: str) -> str:
    value = form.get(key, default_value)
    return value if isinstance(value, str) else default_value


def sanitize_filename(name: str) -> str:
    clean = "".join(char for char in name if char not in '<>:"/\\|?*').strip()
    return clean or "upload.bin"


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:3] + "..." + value[-4:]


def clean_log_line(line: str) -> str:
    project = str(PROJECT_ROOT)
    project_alt = project.replace("\\", "/")
    return line.replace(project, ".").replace(project_alt, ".")


def _job_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")
    env["HF_HUB_CACHE"] = str(PROJECT_ROOT / ".cache" / "huggingface" / "hub")
    src = PROJECT_ROOT / "src"
    env["PYTHONPATH"] = ";".join(str(src / sub) for sub in ["core", "pipeline", "config", "web", "tools"])
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    _clear_proxy_env(env)
    add_project_cuda_to_env(env)
    return env


def _clear_proxy_env(env: dict[str, str]) -> None:
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
