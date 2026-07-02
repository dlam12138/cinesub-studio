from quality_checker import (
    QualityReport,
    SrtEntry,
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
