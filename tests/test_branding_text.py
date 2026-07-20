from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
INDEX_HTML = ROOT / "web" / "index.html"
DESKTOP_MAIN = ROOT / "desktop" / "main.js"
DESKTOP_PACKAGE = ROOT / "desktop" / "package.json"
DESKTOP_README = ROOT / "desktop" / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    assert start_index != -1, start
    assert end_index != -1, end
    return text[start_index:end_index]


def test_web_index_uses_v0_3_3_branding():
    html = _read(INDEX_HTML)
    assert "智译字幕工坊" in html
    assert "CineSub Studio" in html
    assert "<title>智译字幕工坊 — AI 字幕生成器</title>" in html
    assert "本地优先的视频字幕识别与大模型翻译工作台" in html
    assert "<h1>智译字幕工坊</h1>" in html
    assert "<strong>CineSub Studio</strong>" in html
    assert "<h1>字幕工坊</h1>" not in html
    assert "字幕工坊 — 视频字幕生成器" not in html


def test_desktop_branding_surfaces_use_new_display_name():
    main = _read(DESKTOP_MAIN)
    package = json.loads(_read(DESKTOP_PACKAGE))
    readme = _read(DESKTOP_README)
    display_brand = "智译字幕工坊 / CineSub Studio"

    assert display_brand in main
    assert f'title: "{display_brand}"' in main
    assert package["name"] == "cinesub-studio-desktop"
    # v0.5 installer preview uses Chinese-only productName for Windows UI
    assert package["productName"] == "智译字幕工坊"
    assert display_brand in package["description"]
    assert display_brand in readme


def test_provider_ui_stays_llm_only_without_future_media_controls():
    html = _read(INDEX_HTML)
    provider_section = _section(html, 'id="tab-providers"', 'id="tab-langprofiles"')
    combined = html.lower()

    assert "whisper_model" not in provider_section.lower()
    assert "whisper_device" not in provider_section.lower()
    for marker in (
        "tts",
        "dubbing",
        "voice clone",
        "lip-sync",
        "model hub",
        "model store",
        "model installer",
        "downloader",
    ):
        assert marker not in combined
