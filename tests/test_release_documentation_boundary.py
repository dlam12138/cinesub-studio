from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_user_readme_matches_public_source_and_portable_release():
    readme = _read("README.md")
    for marker in (
        "CineSubStudio-0.6.2-windows-x64-portable.zip",
        "CineSubStudio.exe",
        "data/config/",
        "data/output/",
        "自动检测（默认）",
        "固定单语言",
        "多语言",
        "翻译 Provider",
        "AGENTS.md",
    ):
        assert marker in readme
    for internal_marker in (
        "M13",
        "acceptance/",
        "research/",
        "TRIAL.md",
        "project_evaluation_report",
    ):
        assert internal_marker not in readme


def test_stage3_research_tools_are_excluded_from_portable_backend():
    package = _read("desktop/package.json")
    for marker in (
        "!src/tools/asr_quality_evaluation.py",
        "!src/tools/public_gold_sample_prep.py",
        "!src/tools/public_gold_translation_review.py",
        "!src/tools/real_media_acceptance.py",
        "!src/tools/translation_quality_benchmark.py",
    ):
        assert marker in package


def test_agent_guide_describes_current_packaged_and_source_boundaries():
    guide = _read("AGENTS.md")
    for marker in (
        "resources/app/backend/",
        "resources/app/python/",
        "resources/app/tools/",
        "EXE 同级 `data/`",
        "scripts/build_portable_release.py",
        "electron-builder --win --dir",
        "acceptance/",
        "research/",
        "local_files_only=True",
    ):
        assert marker in guide
    assert "未来 release" not in guide


def test_desktop_and_in_zip_instructions_match_the_release_contract():
    desktop = _read("desktop/README.md")
    builder = _read("scripts/build_portable_release.py")
    for text in (desktop, builder):
        assert "CineSubStudio.exe" in text
        assert "small" in text
        assert "data" in text
        assert "CUDA" in text
    assert "不生成 NSIS" in desktop
    assert "同名 .sha256" in builder


def test_third_party_notices_do_not_pin_a_stale_product_version():
    notices = _read("packaging/windows/THIRD_PARTY_NOTICES.md")

    assert "CineSub Studio v0.6.2" not in notices
    for marker in ("Electron", "FFmpeg", "CUDA", "faster-whisper", "WenYi"):
        assert marker in notices
