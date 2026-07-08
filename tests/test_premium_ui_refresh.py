from __future__ import annotations

from pathlib import Path


HTML_PATH = Path(__file__).parent.parent / "web" / "index.html"


def _read_index_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _section(html: str, start: str, end: str) -> str:
    start_index = html.find(start)
    end_index = html.find(end)
    assert start_index != -1, start
    assert end_index != -1, end
    assert start_index < end_index
    return html[start_index:end_index]


def test_premium_app_shell_and_sidebar_navigation_exist():
    html = _read_index_html()
    assert 'data-refresh="v0.2-premium-ui-refresh"' in html
    assert "CineSub Studio" in html
    assert "字幕工坊" in html
    assert "Local Web App" in html
    assert "v0.2 Preview" in html
    for label in ("开始处理", "最近任务", "运行环境", "翻译接口", "语言风格"):
        assert label in html


def test_start_workspace_keeps_batch_and_single_file_entrypoints_separate():
    html = _read_index_html()
    assert 'id="tab-pipeline"' in html
    assert 'id="tab-transcribe" class="tab-content active"' in html
    assert "pipelineAction('run')" in html
    assert 'id="btn-run"' in html
    assert 'id="jobForm"' in html
    assert 'id="startBtn"' in html
    assert 'fetch("/api/jobs"' in html
    assert "fetch('/api/pipeline/" in html


def test_workflow_controls_and_status_surfaces_remain_present():
    html = _read_index_html()
    for needle in (
        'id="pipelineInputDir"',
        'id="pipelineModel"',
        'id="pipelineProviderSelect"',
        'id="path"',
        'id="model"',
        'id="device"',
        'id="translation_mode"',
        'id="runtime-summary-result"',
    ):
        assert needle in html
    assert "选择视频 → 语音识别 → 翻译 → 输出字幕" in html


def test_recent_jobs_runtime_provider_and_profile_sections_exist():
    html = _read_index_html()
    for needle in (
        'id="tab-jobs"',
        'id="jobQueuePanel"',
        'id="jobQueueList"',
        'id="tab-runtime"',
        'id="diagnostics-result"',
        'id="tab-providers"',
        'id="providerList"',
        'id="tab-langprofiles"',
        'id="lpList"',
    ):
        assert needle in html
    assert "总体状态" in html
    assert "API Key 状态" in html
    assert "语音识别默认值" in html


def test_provider_ui_remains_llm_only():
    html = _read_index_html()
    provider_section = _section(html, 'id="tab-providers"', 'id="tab-langprofiles"')
    assert "翻译接口只管理 LLM/API" in provider_section
    assert "whisper_model" not in provider_section.lower()
    assert "whisper_device" not in provider_section.lower()
    assert "ASR 默认值" not in provider_section


def test_language_profile_owns_asr_style_glossary_and_subtitle_preferences():
    html = _read_index_html()
    profile_section = _section(html, 'id="tab-langprofiles"', 'id="lpModal"')
    modal_section = _section(html, 'id="lpModal"', 'id="providerModal"')
    for label in ("基本信息", "语音识别默认值", "翻译风格", "术语表", "字幕输出"):
        assert label in profile_section
    assert "Whisper 模型" in modal_section
    assert "Glossary terms" in modal_section


def test_no_unfinished_or_external_project_ui_was_introduced():
    html = _read_index_html()
    lower = html.lower()
    forbidden = (
        "whispersubtranslate",
        "model hub",
        "model store",
        "model installer",
        "model management",
        "dubbing",
        "tts",
        "voice clone",
        "lip.sync",
        "配音",
        "语音克隆",
        "口型",
    )
    for needle in forbidden:
        assert needle not in lower


def test_api_keys_are_not_rendered_as_raw_values():
    html = _read_index_html()
    assert "sk-m12-secret-should-not-leak" not in html
    assert 'id="api_key" name="api_key" type="password"' in html
    assert "api_key_masked" in html
