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
    sidebar_nav = _section(html, '<nav class="tabs rail-nav">', "</nav>")
    assert 'data-refresh="v0.6-stage2-ui"' in html
    assert "CineSub Studio" in html
    assert "智译字幕工坊" in html
    assert "本地字幕工作站" in html
    assert 'id="appVersionChip"' in html
    assert 'id="appFlavorChip"' in html
    for label in ("批量处理", "单个处理", "运行环境", "翻译接口", "语言风格"):
        assert label in sidebar_nav
    assert "最近任务" not in sidebar_nav
    assert "开始处理" not in sidebar_nav
    for number in ("01", "02", "03", "04", "05"):
        assert number not in sidebar_nav


def test_start_workspace_keeps_batch_and_single_file_entrypoints_separate():
    html = _read_index_html()
    assert 'id="tab-pipeline"' in html
    assert 'id="tab-pipeline" class="tab-content active"' in html
    assert 'id="tab-transcribe" class="tab-content">' in html
    assert "pipelineAction('run')" in html
    assert 'id="btn-run"' in html
    assert 'id="jobForm"' in html
    assert 'id="startBtn"' in html
    assert 'fetch("/api/jobs"' in html
    assert "fetch('/api/pipeline/" in html


def test_pipeline_and_transcribe_tabs_activate_independently():
    html = _read_index_html()
    switch_tab = _section(html, "function switchTab(name)", "async function loadProviders()")
    pipeline_branch = _section(
        switch_tab,
        "if (name === 'pipeline')",
        "} else if (name === 'transcribe')",
    )
    transcribe_branch = _section(
        switch_tab,
        "} else if (name === 'transcribe')",
        "} else {",
    )
    assert "activeTabs['tab-pipeline'] = true;" in pipeline_branch
    assert "activeTabs['tab-transcribe'] = true;" not in pipeline_branch
    assert "activeTabs['tab-transcribe'] = true;" in transcribe_branch
    assert "if (name === 'transcribe') { loadProviderSelect(); loadLangProfileSelect(); }" in switch_tab
    assert "if (name === 'pipeline') loadPipelineSelects();" in switch_tab
    assert "loadJobQueue" not in switch_tab
    assert "loadTopStatus" not in switch_tab


def test_workflow_controls_and_current_job_status_surface_remain_present():
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
        'id="badge"',
        'id="log"',
        'id="downloadSource"',
        'id="downloadTranslated"',
    ):
        assert needle in html
    assert 'class="single-result-panel"' in html
    assert "选择视频 → 语音识别 → 翻译 → 输出字幕" not in html


def test_recent_jobs_ui_is_removed_but_runtime_provider_and_profile_sections_exist():
    html = _read_index_html()
    for removed in (
        'data-tab="jobs"',
        'id="tab-jobs"',
        'id="jobQueuePanel"',
        'id="jobQueueList"',
        "loadJobQueue",
        "openJobTaskDrawer",
        "retryJob",
        'class="topbar"',
        'class="status-strip"',
        'id="statusProvider"',
        'id="statusProfile"',
        'id="statusTempSpace"',
        'id="statusModelCache"',
    ):
        assert removed not in html
    for needle in (
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


def test_single_file_layout_has_wide_form_compact_result_and_responsive_stacking():
    html = _read_index_html()
    assert "grid-template-columns: minmax(0, 1fr) 380px;" in html
    assert "align-items: start;" in html
    assert "height: clamp(240px, 32vh, 320px);" in html
    assert "@media (max-width: 1280px)" in html
    assert ".single-result-panel {\n        position: static;" in html
    assert ".transcribe-layout .row { grid-template-columns: 1fr; }" in html


def test_runtime_has_read_only_missing_component_guidance():
    html = _read_index_html()
    runtime_section = _section(html, 'id="tab-runtime"', 'id="tab-transcribe"')
    assert "缺失组件处理" in runtime_section
    assert "tools/ffmpeg/" in runtime_section
    assert "CINESUB_FFMPEG" in runtime_section
    assert "FFMPEG_PATH" in runtime_section
    assert "models/" in runtime_section
    assert "Hugging Face cache" in runtime_section
    assert "模型下载源" in runtime_section
    assert "只使用本地已下载模型" in runtime_section
    assert "download_model_file.py" not in runtime_section
    assert "model hub" not in runtime_section.lower()
    assert "自动修复" not in runtime_section
    guidance_section = runtime_section[runtime_section.find("缺失组件处理") :]
    assert "<button" not in guidance_section


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
    assert "术语表" in modal_section
    assert 'id="lpEditStyle"' not in modal_section


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
        "auto-fix",
        "automatic repair",
        "自动修复",
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
