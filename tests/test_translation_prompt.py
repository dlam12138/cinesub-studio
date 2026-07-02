from subtitle_translate import (
    _build_effective_prompt,
    _translation_cache_path,
    build_effective_translation_prompt,
)


def test_build_effective_prompt_returns_default_when_custom_prompt_is_blank():
    assert _build_effective_prompt("default prompt", " \n\t ") == "default prompt"


def test_build_effective_prompt_strips_and_appends_custom_prompt():
    effective = _build_effective_prompt("default prompt", "  keep names consistent  \n")

    assert effective.startswith("default prompt\n\n")
    assert "keep names consistent" in effective
    assert "  keep names consistent" not in effective
    assert not effective.endswith("  ")


def test_profile_prompt_builder_uses_style_when_custom_is_blank():
    effective = build_effective_translation_prompt(
        style_prompt="profile style",
        custom_prompt=" \n",
        glossary=[],
    )

    assert effective == "profile style"


def test_profile_prompt_builder_custom_overrides_style_but_keeps_glossary():
    effective = build_effective_translation_prompt(
        style_prompt="profile style should not appear",
        custom_prompt="custom instruction",
        glossary=[{"source": "Jean", "target": "让", "note": "name"}],
    )

    assert "custom instruction" in effective
    assert "profile style should not appear" not in effective
    assert "Glossary terms" in effective
    assert "Jean => 让 (name)" in effective


def test_profile_prompt_builder_can_return_glossary_only():
    effective = build_effective_translation_prompt(
        style_prompt="",
        custom_prompt="",
        glossary=[{"source": "ONU", "target": "联合国", "note": ""}],
    )

    assert effective.startswith("Glossary terms")
    assert "ONU => 联合国" in effective


def test_translation_cache_key_varies_by_effective_prompt(tmp_path):
    input_path = tmp_path / "sample.srt"
    input_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    base = {
        "api_provider": "openai-compatible",
        "llm_model": "model-a",
        "target_language": "zh-CN",
        "translation_mode": "bilingual",
    }
    style_prompt = build_effective_translation_prompt("style one", "", [])
    glossary_prompt = build_effective_translation_prompt(
        "style one",
        "",
        [{"source": "Jean", "target": "让", "note": ""}],
    )

    style_cache = _translation_cache_path(input_path, effective_prompt=style_prompt, **base)
    glossary_cache = _translation_cache_path(input_path, effective_prompt=glossary_prompt, **base)

    assert style_cache != glossary_cache
