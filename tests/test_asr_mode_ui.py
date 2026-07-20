from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(
    encoding="utf-8"
)


def test_single_and_batch_use_the_same_three_asr_modes() -> None:
    for value in ("auto", "fixed", "multilingual"):
        assert f'name="asr_mode" value="{value}"' in HTML
        assert f'name="pipelineAsrMode" value="{value}"' in HTML
    assert HTML.count("自动检测") >= 2
    assert HTML.count("固定单语言") >= 2
    assert HTML.count("多语言") >= 2
    assert 'id="singleFixedLanguagePanel"' in HTML
    assert 'id="pipelineFixedLanguagePanel"' in HTML


def test_old_asr_product_controls_and_prompt_editors_are_absent() -> None:
    for retired in (
        'id="asr_recognizer"',
        'id="asr_aligner"',
        'id="pipelineAsrRecognizer"',
        'id="pipelineAsrAligner"',
        "segment_asr_routing",
        'id="translation_prompt"',
        'id="lpEditStyle"',
        'id="localeEnButton"',
    ):
        assert retired not in HTML
    assert 'id="lpEditGlossary"' in HTML
    assert 'id="lpExistingStyle"' in HTML


def test_narrow_layout_avoids_horizontal_stage_ribbon() -> None:
    assert ".asr-mode-rail { grid-template-columns: 1fr; }" in HTML
    assert (
        ".stage6-stage-ribbon { grid-template-columns: repeat(2, minmax(0,1fr)); "
        "overflow-x: visible; }"
    ) in HTML
