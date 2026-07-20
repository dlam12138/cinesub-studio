from quality_checker import QualityReport, SrtEntry, _check_duplicates


def _entry(index: int, text: str) -> SrtEntry:
    return SrtEntry(
        index=index,
        start_time=float(index),
        end_time=float(index + 1),
        time_line=f"00:00:0{index},000 --> 00:00:0{index + 1},000",
        text=text,
    )


def test_two_exact_repeats_and_adjacent_near_repeats_are_reported():
    report = QualityReport()
    _check_duplicates(
        [
            _entry(1, "This is an exact repeated subtitle."),
            _entry(2, "This is an exact repeated subtitle."),
            _entry(3, "A sufficiently long subtitle candidate."),
            _entry(4, "A sufficiently long subtitle candidates."),
        ],
        report,
    )

    issue_types = [issue.type for issue in report.issues]
    assert "duplicate_content" in issue_types
    assert "near_duplicate_content" in issue_types
