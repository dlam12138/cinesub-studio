from subtitle_translate import SubtitleItem, read_srt, write_srt


def test_read_srt_parses_entries_and_multiline_text(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(
        "\n".join(
            [
                "1",
                "00:00:01,000 --> 00:00:02,000",
                "Hello.",
                "",
                "2",
                "00:00:03,000 --> 00:00:05,000",
                "Line one.",
                "Line two.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    items = read_srt(source)

    assert len(items) == 2
    assert items[0].index == 1
    assert items[0].time_line == "00:00:01,000 --> 00:00:02,000"
    assert items[0].text == "Hello."
    assert items[1].text == "Line one.\nLine two."


def test_read_srt_accepts_utf8_bom_and_chinese_path(tmp_path):
    source_dir = tmp_path / "中文目录"
    source_dir.mkdir()
    source = source_dir / "电影字幕.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n你好。\n\n",
        encoding="utf-8-sig",
    )

    items = read_srt(source)

    assert len(items) == 1
    assert items[0].text == "你好。"


def test_write_srt_preserves_original_text_and_appends_translation(tmp_path):
    output = tmp_path / "nested" / "bilingual.srt"
    items = [
        SubtitleItem(
            index=1,
            time_line="00:00:01,000 --> 00:00:02,000",
            text="Hello.",
            translation="你好。",
        )
    ]

    write_srt(items, output)

    written = output.read_text(encoding="utf-8")
    assert "1\n00:00:01,000 --> 00:00:02,000\nHello.\n你好。\n\n" == written
    reread = read_srt(output)
    assert reread[0].text == "Hello.\n你好。"
