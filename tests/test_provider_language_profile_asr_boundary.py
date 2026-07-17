from __future__ import annotations

import json
import sys

import pytest

import batch_worker
import job_api
import language_profile_store
import provider_store


def _reset_provider_cache() -> None:
    with provider_store._cache_lock:
        provider_store._cache = None
        provider_store._cache_mtime = 0.0


@pytest.fixture
def isolated_provider_store(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_path = config_dir / "providers.local.json"
    monkeypatch.setattr(provider_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(provider_store, "CONFIG_PATH", config_path)
    _reset_provider_cache()
    try:
        yield config_path
    finally:
        _reset_provider_cache()


def _write_provider_config(config_path, provider: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "version": 2,
                "active": provider["id"],
                "providers": [provider],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _reset_provider_cache()


def test_legacy_provider_asr_fields_are_ignored_when_resolving(isolated_provider_store):
    _write_provider_config(
        isolated_provider_store,
        {
            "id": "legacy-main",
            "name": "Legacy Provider",
            "protocol": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": "sk-test",
            "translation_model": "llm-test",
            "whisper_model": "provider-small",
            "whisper_device": "cpu",
            "enabled": True,
        },
    )

    provider = provider_store.get_provider("legacy-main")
    resolved = provider_store.resolve_provider_config("legacy-main")

    assert provider is not None
    assert "whisper_model" not in provider
    assert "whisper_device" not in provider
    assert resolved == {
        "api_provider": "openai-compatible",
        "api_base": "https://example.invalid/v1",
        "api_key": "sk-test",
        "llm_model": "llm-test",
        "translation_quality_model": "llm-test",
    }


def test_upsert_provider_scrubs_legacy_asr_fields(isolated_provider_store):
    saved = provider_store.upsert_provider(
        {
            "id": "clean-main",
            "name": "Clean Provider",
            "protocol": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": "sk-test",
            "model": "llm-test",
            "translation_quality_model": "llm-quality",
            "whisper_model": "provider-small",
            "whisper_device": "cuda",
            "enabled": True,
        }
    )

    raw_text = isolated_provider_store.read_text(encoding="utf-8")

    assert "whisper_model" not in saved
    assert "whisper_device" not in saved
    assert "whisper_model" not in raw_text
    assert "whisper_device" not in raw_text
    assert saved["translation_quality_model"] == "llm-quality"
    assert provider_store.resolve_provider_config("clean-main")[
        "translation_quality_model"
    ] == "llm-quality"


def test_provider_example_config_does_not_expose_asr_fields():
    example = (provider_store.PROJECT_ROOT / "config" / "providers.local.json.example").read_text(
        encoding="utf-8"
    )

    assert "whisper_model" not in example
    assert "whisper_device" not in example


def test_batch_asr_uses_language_profile_over_legacy_provider(monkeypatch):
    captured = {}

    def fake_provider_config(provider_id=None):
        assert provider_id == "legacy-main"
        return {
            "api_provider": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": "sk-test",
            "llm_model": "llm-test",
            "whisper_model": "provider-small",
            "whisper_device": "cpu",
        }

    def fake_profile_config(profile_id=None):
        assert profile_id == "film-profile"
        return {
            "profile_id": "film-profile",
            "profile_name": "Film Profile",
            "source_language": "fr",
            "target_language": "zh-CN",
            "asr": {
                "whisper_model": "profile-large",
                "whisper_device": "cuda",
                "compute_type": "float16",
                "language": "fr",
                "beam_size": 6,
                "vad_filter": False,
            },
            "quality": {},
            "translation_style": "",
            "glossary": [],
            "subtitle_style": {},
            "llm_stages": {},
        }

    class FakePipeline:
        def __init__(self, config):
            captured["config"] = config

        def scan(self):
            return []

    monkeypatch.setattr(provider_store, "resolve_provider_config", fake_provider_config)
    monkeypatch.setattr(language_profile_store, "resolve_language_profile_config", fake_profile_config)
    monkeypatch.setattr(batch_worker, "BatchPipeline", FakePipeline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "batch_worker.py",
            "--scan",
            "--provider",
            "legacy-main",
            "--language-profile",
            "film-profile",
            "--no-translate",
        ],
    )

    assert batch_worker.main() == 0
    config = captured["config"]
    assert config.model == "profile-large"
    assert config.device == "cuda"
    assert config.compute_type == "float16"
    assert config.language == "fr"
    assert config.beam_size == 6
    assert config.vad_filter is False


def test_batch_explicit_cli_asr_overrides_language_profile(monkeypatch):
    captured = {}

    def fake_profile_config(profile_id=None):
        assert profile_id == "film-profile"
        return {
            "profile_id": "film-profile",
            "profile_name": "Film Profile",
            "source_language": "fr",
            "target_language": "zh-CN",
            "asr": {
                "whisper_model": "profile-large",
                "whisper_device": "cuda",
                "compute_type": "float16",
                "language": "fr",
                "beam_size": 6,
                "vad_filter": True,
            },
            "quality": {},
            "translation_style": "",
            "glossary": [],
            "subtitle_style": {},
            "llm_stages": {},
        }

    class FakePipeline:
        def __init__(self, config):
            captured["config"] = config

        def scan(self):
            return []

    monkeypatch.setattr(language_profile_store, "resolve_language_profile_config", fake_profile_config)
    monkeypatch.setattr(batch_worker, "BatchPipeline", FakePipeline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "batch_worker.py",
            "--scan",
            "--no-translate",
            "--language-profile",
            "film-profile",
            "--model",
            "cli-small",
            "--device",
            "cpu",
            "--compute-type",
            "int8",
            "--language",
            "ja",
            "--beam-size",
            "7",
            "--no-vad",
        ],
    )

    assert batch_worker.main() == 0
    config = captured["config"]
    assert config.model == "cli-small"
    assert config.device == "cpu"
    assert config.compute_type == "int8"
    assert config.language == "ja"
    assert config.beam_size == 7
    assert config.vad_filter is False


def test_web_single_file_job_ignores_legacy_provider_asr_fields(monkeypatch, tmp_path):
    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    def fake_provider_config(provider_id=None):
        assert provider_id == "legacy-main"
        return {
            "api_provider": "openai-compatible",
            "api_base": "https://example.invalid/v1",
            "api_key": "sk-test",
            "llm_model": "llm-test",
            "whisper_model": "provider-small",
            "whisper_device": "cuda",
        }

    monkeypatch.setattr(provider_store, "resolve_provider_config", fake_provider_config)

    job = job_api.create_job(
        {
            "path": str(media),
            "model": "form-small",
            "device": "cpu",
            "compute_type": "int8",
            "language": "en",
            "beam_size": "4",
            "vad": "on",
            "translate_enabled": "on",
            "provider_select": "legacy-main",
        }
    )

    assert job["options"]["model"] == "form-small"
    assert job["options"]["device"] == "cpu"
    assert job["options"]["compute_type"] == "int8"
    assert job["options"]["language"] == "en"
    assert job["options"]["beam_size"] == 4
    assert job["options"]["api_base"] == "https://example.invalid/v1"
    assert job["options"]["llm_model"] == "llm-test"
