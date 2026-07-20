import sys

import pytest
from encoding_utils import read_json, read_text, run_text, write_json, write_text


def test_read_text_accepts_utf8_bom_for_user_input(tmp_path):
    path = tmp_path / "bom.srt"
    path.write_text("\ufeff1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

    text = read_text(path, user_input=True)

    assert text.startswith("1\n")
    assert "你好" in text


def test_write_text_preserves_chinese(tmp_path):
    path = tmp_path / "中文输出.txt"

    write_text(path, "路径正常\n字幕正常")

    assert path.read_text(encoding="utf-8") == "路径正常\n字幕正常"


def test_json_round_trip_preserves_chinese_path_and_content(tmp_path):
    path = tmp_path / "中文目录" / "任务.state.json"
    path.parent.mkdir()
    payload = {"file": "电影 输入.mp4", "status": "failed", "error": "路径包含中文"}

    write_json(path, payload)

    assert "\\u4e2d" not in path.read_text(encoding="utf-8")
    assert read_json(path) == payload


def test_run_text_replaces_undecodable_output():
    result = run_text(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"],
        capture_output=True,
        check=True,
    )

    assert result.stdout == "\ufffd"


def test_run_text_rejects_conflicting_kwargs():
    with pytest.raises(TypeError):
        run_text([sys.executable, "-c", "pass"], capture_output=True, encoding="gbk")
