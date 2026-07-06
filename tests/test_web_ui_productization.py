from __future__ import annotations

import pytest

HTML_PATH = __import__("pathlib").Path(__file__).parent.parent / "web" / "index.html"


def _read_index_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


# ── 1. Page loads and has correct title ──


def test_index_page_has_product_title():
    html = _read_index_html()
    assert "<title>字幕工坊" in html
    assert "视频字幕生成器" in html


# ── 2. Workflow sections are present ──


def test_has_input_section():
    html = _read_index_html()
    assert "选择视频" in html or "input" in html.lower()


def test_has_speech_recognition_section():
    html = _read_index_html()
    assert "语音识别" in html
    assert "语音识别模型" in html
    assert "运行设备" in html


def test_has_translation_provider_section():
    html = _read_index_html()
    assert "翻译接口" in html
    assert "翻译设置" in html


def test_has_language_profile_section():
    html = _read_index_html()
    assert "语言风格" in html or "语言配置" in html


def test_has_output_section():
    html = _read_index_html()
    assert "输出格式" in html or "translation_mode" in html
    assert "双语" in html or "bilingual" in html


def test_has_job_status_section():
    html = _read_index_html()
    assert "任务状态" in html
    assert "job-result" in html or "jobForm" in html


def test_has_workflow_steps():
    html = _read_index_html()
    assert "Step 1" in html
    assert "Step 2" in html
    assert "Step 3" in html
    assert "Step 4" in html


# ── 3. Provider UI does not expose legacy ASR fields ──


def test_provider_modal_no_whisper_model():
    html = _read_index_html()
    provider_modal_start = html.find('id="providerModal"')
    provider_modal = html[provider_modal_start:provider_modal_start + 15000]
    assert "whisper_model" not in provider_modal.lower()
    assert "whisper_device" not in provider_modal.lower()


def test_provider_table_no_asr_columns():
    html = _read_index_html()
    provider_section = html[html.find('id="tab-providers"'):html.find('id="tab-langprofiles"')]
    assert "whisper_model" not in provider_section.lower()
    assert "whisper_device" not in provider_section.lower()


# ── 4. Job API returns user-facing status fields ──


def test_create_job_returns_expected_fields(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    job = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "on",
        "provider_select": "",
        "api_base": "https://example.invalid/v1",
        "api_key": "sk-test",
        "llm_model": "test-model",
    })

    assert "id" in job
    assert "status" in job
    assert job["status"] == "queued"
    assert "options" in job
    assert "source_output" in job
    assert "translated_output" in job
    assert "logs" in job
    assert "returncode" in job


def test_get_job_returns_safe_fields(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })

    safe = job_api.get_job(raw["id"])
    assert safe is not None
    assert "_api_key" not in safe
    assert safe["status"] == "queued"
    assert "options" in safe
    assert "source_output" in safe
    assert "translated_output" in safe


# ── 5. Failed job includes readable error summary ──


def test_failed_job_has_logs_and_status():
    import job_api

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    job_id = "test-fail-001"
    with job_api.JOBS_LOCK:
        job_api.JOBS[job_id] = {
            "id": job_id,
            "status": "failed",
            "created_at": 0,
            "updated_at": 0,
            "input": "/tmp/test.mkv",
            "output": "",
            "source_output": "",
            "translated_output": "",
            "quality_report": "",
            "review_needed": "",
            "returncode": 1,
            "segment_asr_routing_status": "",
            "segment_asr_routing_report": "",
            "segment_asr_routing_message": "",
            "options": {},
            "_api_key": "secret",
            "logs": ["Started.", "FFmpeg error: codec not found", "Failed with code 1."],
        }

    safe = job_api.get_job(job_id)
    assert safe["status"] == "failed"
    assert "logs" in safe
    assert "FFmpeg error" in " ".join(safe["logs"])
    assert "_api_key" not in safe


# ── 6. Provider / Language Profile selectors exist ──


def test_provider_select_element_exists():
    html = _read_index_html()
    assert 'id="provider_select"' in html
    assert 'id="pipelineProviderSelect"' in html


def test_language_profile_select_element_exists():
    html = _read_index_html()
    assert 'id="lang_profile_select"' in html
    assert 'id="pipelineLangProfileSelect"' in html


# ── 7. UI does not expose unfinished dubbing / TTS controls ──


def test_no_dubbing_controls():
    html = _read_index_html()
    lower = html.lower()
    assert "配音" not in lower
    assert "dubbing" not in lower
    assert "tts" not in lower
    assert "语音克隆" not in lower
    assert "voice clone" not in lower
    assert "lip.sync" not in lower
    assert "口型" not in lower


def test_no_audio_mixing_controls():
    html = _read_index_html()
    lower = html.lower()
    assert "混音" not in lower
    assert "audio mixing" not in lower
    assert "muxing" not in lower


def test_no_model_management_ui():
    html = _read_index_html()
    lower = html.lower()
    assert "模型管理" not in lower
    assert "model management" not in lower
