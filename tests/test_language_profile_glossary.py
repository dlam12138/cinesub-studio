import json

import language_profile_store


SENTINEL = "sk-test-M5-SECRET-SHOULD-NOT-LEAK"


def _patch_store(monkeypatch, tmp_path):
    config_path = tmp_path / "config" / "language_profiles.local.json"
    monkeypatch.setattr(language_profile_store, "CONFIG_PATH", config_path)
    language_profile_store._clear_cache()
    return config_path


def test_builtin_profiles_default_to_empty_glossary(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)

    profile = language_profile_store.get_language_profile("auto-detect")
    resolved = language_profile_store.resolve_language_profile_config("auto-detect")

    assert profile["glossary"] == []
    assert resolved["glossary"] == []


def test_local_profile_persists_normalized_glossary_and_removes_secrets(monkeypatch, tmp_path):
    config_path = _patch_store(monkeypatch, tmp_path)

    saved = language_profile_store.upsert_language_profile(
        {
            "id": "film-local",
            "name": "Film Local",
            "target_language": "zh-CN",
            "api_key": SENTINEL,
            "nested": {"access_token": SENTINEL},
            "glossary": [
                {"source": " Jean ", "target": " 让 ", "note": " name "},
                {"source": "Jean", "target": "让", "note": "duplicate"},
                {"source": "Only source", "target": ""},
                {"source": "", "target": "Only target"},
                {"source": "ONU", "target": "联合国", "note": ""},
            ],
        }
    )

    assert saved["glossary"] == [
        {"source": "Jean", "target": "让", "note": "name"},
        {"source": "ONU", "target": "联合国", "note": ""},
    ]

    raw_text = config_path.read_text(encoding="utf-8")
    assert SENTINEL not in raw_text
    raw = json.loads(raw_text)
    assert "api_key" not in json.dumps(raw, ensure_ascii=False)
    assert "access_token" not in json.dumps(raw, ensure_ascii=False)

    resolved = language_profile_store.resolve_language_profile_config("film-local")
    assert resolved["glossary"] == saved["glossary"]


def test_normalize_glossary_drops_incomplete_rows_and_dedupes():
    rows = language_profile_store.normalize_glossary(
        [
            {"source": " A ", "target": " B ", "note": " n "},
            {"source": "A", "target": "B", "note": "later"},
            {"source": "A", "target": ""},
            {"source": "", "target": "B"},
            "not a row",
        ]
    )

    assert rows == [{"source": "A", "target": "B", "note": "n"}]
