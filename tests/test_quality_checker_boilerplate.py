from quality_checker import (
    QualityReport,
    SrtEntry,
    check_source_srt,
    _check_llm_boilerplate,
    _finalize_report,
)


def _check_texts(texts: list[str]) -> QualityReport:
    entries = [
        SrtEntry(
            index=index,
            start_time=float(index),
            end_time=float(index + 1),
            time_line="00:00:00,000 --> 00:00:01,000",
            text=text,
        )
        for index, text in enumerate(texts, start=1)
    ]
    report = QualityReport(total_entries=len(entries))
    _check_llm_boilerplate(entries, report)
    _finalize_report(report)
    return report


def test_llm_boilerplate_does_not_flag_normal_dialogue():
    report = _check_texts([
        "好的，很高兴认识你。",
        "当然，别忘了。",
        "嗯，当然。",
        "Sure.",
        "Ok.",
    ])

    assert [issue.type for issue in report.issues] == []


def test_llm_boilerplate_still_flags_assistant_meta_output():
    report = _check_texts([
        "好的，以下是翻译：你好。",
        "Sure, here is the translation: Hello.",
    ])

    assert [issue.type for issue in report.issues] == ["llm_boilerplate", "llm_boilerplate"]
    assert report.status == "fail"


def _write_minimal_srt(path):
    path.write_text(
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "Bonjour.\n",
        encoding="utf-8",
    )


def test_source_language_mismatch_uses_lang_json_source_language(tmp_path):
    srt_path = tmp_path / "sample.srt"
    _write_minimal_srt(srt_path)

    report = check_source_srt(
        srt_path,
        lang_json={
            "source_language": "en",
            "forced_language": "fr",
            "language_probability": 1.0,
        },
    )

    issues = [issue for issue in report.issues if issue.type == "source_language_mismatch"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_source_language_match_does_not_emit_mismatch(tmp_path):
    srt_path = tmp_path / "sample.srt"
    _write_minimal_srt(srt_path)

    report = check_source_srt(
        srt_path,
        lang_json={
            "source_language": "fr",
            "forced_language": "fr",
            "language_probability": 1.0,
        },
    )

    assert "source_language_mismatch" not in [issue.type for issue in report.issues]
