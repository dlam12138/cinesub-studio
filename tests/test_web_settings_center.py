from __future__ import annotations

import json
from pathlib import Path

import language_profile_store
import provider_store
import web_server
from conftest import MemoryTestServer, json_test_handler

HTML_PATH = Path(__file__).parent.parent / "web" / "index.html"
SECRET = "sk-m12-secret-should-not-leak"


def _read_index_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _reset_provider_cache() -> None:
    with provider_store._cache_lock:
        provider_store._cache = None
        provider_store._cache_mtime = 0.0


def _isolate_provider_store(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_path = config_dir / "providers.local.json"
    monkeypatch.setattr(provider_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(provider_store, "CONFIG_PATH", config_path)
    _reset_provider_cache()
    return config_path


def _isolate_profile_store(monkeypatch, tmp_path):
    config_path = tmp_path / "config" / "language_profiles.local.json"
    monkeypatch.setattr(language_profile_store, "CONFIG_PATH", config_path)
    language_profile_store._clear_cache()
    return config_path


def _serve():
    server = MemoryTestServer()
    return server, server


def _request(base: str, method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if method in {"POST", "PUT", "DELETE"}:
        _status, _session_headers, session = json_test_handler(
            base,
            web_server.Handler,
            method="GET",
            path="/api/session",
        )
        headers["X-CineSub-Token"] = session["token"]
    status, _response_headers, payload = json_test_handler(
        base,
        web_server.Handler,
        method=method,
        path=path,
        headers=headers,
        body=data or b"",
    )
    return status, payload


def test_provider_settings_ui_exists_and_is_llm_only():
    html = _read_index_html()
    provider_section = html[html.find('id="tab-providers"'):html.find('id="tab-langprofiles"')]

    assert "翻译接口只管理 LLM/API" in html
    assert "管理翻译接口" in html
    assert "API 地址" in html
    assert "API Key" in html
    assert "whisper_model" not in provider_section.lower()
    assert "whisper_device" not in provider_section.lower()
    assert "dubbing" not in html.lower()
    assert "tts" not in html.lower()
    assert "model hub" not in html.lower()


def test_language_profile_settings_ui_owns_asr_and_glossary():
    html = _read_index_html()
    profile_section = html[html.find('id="tab-langprofiles"'):html.find('id="lpModal"')]
    modal_section = html[html.find('id="lpModal"'):html.find('id="providerModal"')]

    assert "语言风格管理 ASR 默认值" in html
    assert "管理语言风格" in html
    assert "Whisper" in modal_section
    assert "Glossary terms" in modal_section
    assert "Provider 不参与识别参数" in profile_section


def test_provider_list_get_and_active_are_sanitized(monkeypatch, tmp_path):
    _isolate_provider_store(monkeypatch, tmp_path)
    provider_store.upsert_provider(
        {
            "id": "deepseek-main",
            "name": "DeepSeek",
            "protocol": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": SECRET,
            "model": "deepseek-test",
            "enabled": True,
        }
    )
    provider_store.set_active_provider("deepseek-main")
    server, base = _serve()
    try:
        for path in ("/api/providers", "/api/providers/deepseek-main", "/api/providers/active"):
            status, payload = _request(base, "GET", path)
            text = json.dumps(payload, ensure_ascii=False)
            assert status == 200
            assert SECRET not in text
            assert "api_key" not in text or "api_key_masked" in text
            assert "api_key_masked" in text
    finally:
        server.shutdown()


def test_provider_update_preserves_blank_key_and_scrubs_asr(monkeypatch, tmp_path):
    config_path = _isolate_provider_store(monkeypatch, tmp_path)
    provider_store.upsert_provider(
        {
            "id": "custom-main",
            "name": "Custom",
            "protocol": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": SECRET,
            "model": "model-a",
            "whisper_model": "bad",
            "whisper_device": "bad",
        }
    )
    server, base = _serve()
    try:
        status, payload = _request(
            base,
            "PUT",
            "/api/providers/custom-main",
            {
                "name": "Custom Updated",
                "protocol": "openai-compatible",
                "api_base": "https://example.invalid/v2",
                "api_key": "",
                "model": "model-b",
                "whisper_model": "provider-small",
                "whisper_device": "cuda",
            },
        )
    finally:
        server.shutdown()

    assert status == 200
    assert SECRET not in json.dumps(payload, ensure_ascii=False)
    saved = provider_store.get_provider("custom-main")
    assert saved["api_key"] == SECRET
    assert saved["translation_model"] == "model-b"
    raw = config_path.read_text(encoding="utf-8")
    assert "whisper_model" not in raw
    assert "whisper_device" not in raw


def test_provider_validation_and_connection_test_do_not_leak_secret(monkeypatch, tmp_path):
    _isolate_provider_store(monkeypatch, tmp_path)
    server, base = _serve()
    try:
        status, payload = _request(
            base,
            "POST",
            "/api/providers",
            {"id": "bad-main", "name": "", "api_base": "", "api_key": SECRET, "model": ""},
        )
        assert status == 400
        assert SECRET not in json.dumps(payload, ensure_ascii=False)

        provider_store.upsert_provider(
            {
                "id": "test-main",
                "name": "Test",
                "protocol": "openai-compatible",
                "api_base": "https://example.invalid/v1",
                "api_key": SECRET,
                "model": "model-a",
            }
        )
        monkeypatch.setattr(
            provider_store,
            "test_provider_connection",
            lambda provider_id: {"ok": False, "error": f"network failed {SECRET}", "latency_ms": 0, "model": "model-a"},
        )
        status, payload = _request(base, "POST", "/api/providers/test-main/test")
        text = json.dumps(payload, ensure_ascii=False)
        assert status == 200
        assert SECRET not in text
        assert "redacted" in text
    finally:
        server.shutdown()


def test_language_profile_get_save_delete_uses_local_merge(monkeypatch, tmp_path):
    config_path = _isolate_profile_store(monkeypatch, tmp_path)
    _isolate_provider_store(monkeypatch, tmp_path)
    server, base = _serve()
    profile_payload = {
        "id": "film-local",
        "name": "Film Local",
        "description": "Local profile",
        "source_language": "fr",
        "target_language": "zh-CN",
        "api_key": SECRET,
        "asr": {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "compute_type": "float16",
            "beam_size": 6,
            "vad_filter": False,
        },
        "translation_style": "film style",
        "glossary": [
            {"source": "Jean", "target": "让", "note": "name"},
            {"source": "", "target": "drop"},
        ],
        "subtitle_style": {"formats": ["srt", "ass"], "ass_style_id": "clean-cn"},
    }
    try:
        status, payload = _request(base, "POST", "/api/language-profiles", profile_payload)
        assert status == 201
        assert SECRET not in json.dumps(payload, ensure_ascii=False)

        status, payload = _request(base, "GET", "/api/language-profiles/film-local")
        assert status == 200
        profile = payload["profile"]
        assert profile["asr"]["whisper_model"] == "large-v3"
        assert profile["asr"]["whisper_device"] == "cuda"
        assert profile["asr"]["beam_size"] == 6
        assert profile["glossary"] == [{"source": "Jean", "target": "让", "note": "name"}]
        assert profile["subtitle_style"]["formats"][0] == "srt"

        status, payload = _request(base, "DELETE", "/api/language-profiles/auto-detect")
        assert status == 200
        status, payload = _request(base, "GET", "/api/language-profiles/auto-detect")
        assert status == 200
        assert payload["profile"]["builtin"] is True
    finally:
        server.shutdown()

    assert config_path.exists()
    assert SECRET not in config_path.read_text(encoding="utf-8")


def test_profile_editing_does_not_modify_provider_config(monkeypatch, tmp_path):
    provider_path = _isolate_provider_store(monkeypatch, tmp_path)
    _isolate_profile_store(monkeypatch, tmp_path)
    provider_store.upsert_provider(
        {
            "id": "stable-main",
            "name": "Stable",
            "protocol": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": SECRET,
            "model": "stable-model",
        }
    )
    before = provider_path.read_text(encoding="utf-8")

    language_profile_store.upsert_language_profile(
        {
            "id": "profile-main",
            "name": "Profile",
            "source_language": "auto",
            "target_language": "zh-CN",
            "asr": {"whisper_model": "small", "whisper_device": "cpu"},
        }
    )

    assert provider_path.read_text(encoding="utf-8") == before
