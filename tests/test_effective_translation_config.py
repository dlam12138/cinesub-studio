import json

import language_profile_store
import provider_store
import web_server


SENTINEL = "sk-test-M5-SECRET-SHOULD-NOT-LEAK"


def test_effective_config_preview_masks_provider_secret(monkeypatch):
    monkeypatch.setattr(
        provider_store,
        "get_provider",
        lambda provider_id: {
            "id": provider_id,
            "name": "DeepSeek Test",
            "protocol": "openai-compatible",
            "translation_model": "deepseek-test",
            "api_key": SENTINEL,
            "enabled": True,
        },
    )
    monkeypatch.setattr(provider_store, "get_active_provider", lambda: None)
    monkeypatch.setattr(provider_store, "mask_api_key", lambda key: "sk-...LEAK")
    monkeypatch.setattr(
        language_profile_store,
        "get_language_profile",
        lambda profile_id: {
            "id": profile_id,
            "name": "Profile",
            "source_language": "fr",
            "target_language": "zh-CN",
            "translation_style": "film style",
            "glossary": [{"source": "Jean", "target": "让", "note": ""}],
            "quality": {"language_probability_warning": 0.9},
        },
    )
    monkeypatch.setattr(language_profile_store, "get_active_language_profile", lambda: None)
    monkeypatch.setattr(
        language_profile_store,
        "list_language_profiles",
        lambda: [{"id": "fr-film", "builtin": False}],
    )

    payload = web_server._effective_translation_config(
        {"provider_id": ["deepseek-main"], "language_profile_id": ["fr-film"]}
    )
    text = json.dumps(payload, ensure_ascii=False)

    assert payload["provider"]["api_key_present"] is True
    assert payload["provider"]["api_key_masked"] == "sk-...LEAK"
    assert payload["provider"]["model"] == "deepseek-test"
    assert payload["language_profile"]["glossary_count"] == 1
    assert SENTINEL not in text
    assert "api_key" not in text or "api_key_masked" in text


def test_effective_config_preview_is_read_only_and_handles_missing_config(monkeypatch):
    calls = {"provider": 0, "profile": 0}

    def active_provider():
        calls["provider"] += 1
        return None

    def active_profile():
        calls["profile"] += 1
        return {
            "id": "auto-detect",
            "name": "Auto",
            "source_language": "auto",
            "target_language": "zh-CN",
            "translation_style": "",
            "glossary": [],
            "quality": {},
            "builtin": True,
        }

    monkeypatch.setattr(provider_store, "get_active_provider", active_provider)
    monkeypatch.setattr(provider_store, "get_provider", lambda provider_id: None)
    monkeypatch.setattr(language_profile_store, "get_active_language_profile", active_profile)
    monkeypatch.setattr(language_profile_store, "get_language_profile", lambda profile_id: None)
    monkeypatch.setattr(
        language_profile_store,
        "list_language_profiles",
        lambda: [{"id": "auto-detect", "builtin": True}],
    )

    payload = web_server._effective_translation_config({})

    assert payload["ok"] is True
    assert payload["provider"]["status"] == "not_configured"
    assert payload["language_profile"]["status"] == "ok"
    assert payload["cache_behavior"]["key_includes_effective_prompt"] is True
    assert calls == {"provider": 1, "profile": 1}
