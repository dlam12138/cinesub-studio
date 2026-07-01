from subtitle_translate import _build_effective_prompt


def test_build_effective_prompt_returns_default_when_custom_prompt_is_blank():
    assert _build_effective_prompt("default prompt", " \n\t ") == "default prompt"


def test_build_effective_prompt_strips_and_appends_custom_prompt():
    effective = _build_effective_prompt("default prompt", "  keep names consistent  \n")

    assert effective.startswith("default prompt\n\n")
    assert "keep names consistent" in effective
    assert "  keep names consistent" not in effective
    assert not effective.endswith("  ")
