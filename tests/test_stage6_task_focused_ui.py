from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "index.html"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_stage6_keeps_five_workspaces_and_chinese_first_shell():
    html = _html()
    for tab in ("pipeline", "transcribe", "runtime", "providers", "langprofiles"):
        assert f'data-tab="{tab}"' in html
    assert 'data-tab="jobs"' not in html
    assert "本地字幕工作站" in html
    assert "本地字幕生产控制台" not in html
    assert "选择视频 → 语音识别 → 翻译 → 输出字幕" not in html
    assert 'id="localeZhButton"' not in html
    assert 'id="localeEnButton"' not in html
    assert "English 界面已预留" not in html
    assert "data-i18n-en=" in html


def test_stage6_batch_workspace_is_task_focused():
    html = _html()
    pipeline = html[html.index('id="tab-pipeline"'):html.index("Runtime Diagnostics Tab")]
    assert "stage6-stage-ribbon" in pipeline
    assert "pipeline-task-desk" in pipeline
    assert "pipeline-control-rail" in pipeline
    assert "task-table" in html
    assert "openPipelineTaskDrawer" in html
    assert "ASR Evidence Reports" not in pipeline
    assert 'id="runtime-summary-result"' not in pipeline
    assert 'id="pipeline-log"' not in pipeline
    assert 'id="storage-result"' not in pipeline


def test_stage6_task_drawer_has_accessible_preview_flow():
    html = _html()
    assert 'id="taskDrawer"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert "taskDrawerReturnFocus" in html
    assert "event.key === 'Escape'" in html
    assert "event.key !== 'Tab'" in html
    assert "loadTaskPreview" in html
    assert "/api/pipeline/preview?task=" in html
    assert "/api/jobs/" in html and "/preview?" in html
    assert "text_lines" in html
    assert "上一页" in html and "下一页" in html


def test_stage6_moves_operational_diagnostics_to_runtime_workspace():
    html = _html()
    runtime = html[html.index('id="tab-runtime"'):html.index('id="tab-transcribe"')]
    assert 'id="runtime-summary-result"' in runtime
    assert 'id="pipeline-log"' in runtime
    assert 'id="storage-result"' in runtime
    assert 'id="asr-evidence-result"' not in runtime
    assert "ASR 诊断证据（高级）" not in runtime


def test_stage6_uses_only_existing_local_frontend_delivery():
    html = _html()
    assert "cdn.tailwindcss.com" not in html
    assert "iconify" not in html.lower()
    assert "fonts.googleapis.com" not in html
    assert "/assets/fonts/BarlowCondensed-SemiBold.ttf" in html
    assert "/assets/fonts/NotoSansSC-Variable.ttf" in html
    assert "@media (max-width: 1100px)" in html
    assert "@media (max-width: 900px)" in html
    assert "@media (max-width: 700px)" in html
    assert "prefers-reduced-motion" in html
