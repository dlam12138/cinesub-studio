from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
HTML = ROOT / "web" / "index.html"
PACKAGE = ROOT / "desktop" / "package.json"
BUILD_SCRIPT = ROOT / "packaging" / "windows" / "build_installer.ps1"
MANIFEST_SCRIPT = ROOT / "packaging" / "windows" / "generate_release_manifest.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _manifest_module():
    spec = importlib.util.spec_from_file_location("cinesub_release_manifest", MANIFEST_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage2_package_metadata_and_brand_assets_are_configured():
    package = json.loads(_read(PACKAGE))
    assert package["version"] == "0.6.1"
    assert package["cinesubBuildFlavor"] == "cpu"
    assert package["build"]["win"]["icon"] == "build/icon.ico"
    assert "build/**/*" in package["build"]["files"]
    for relative in (
        "desktop/build/icon.svg",
        "desktop/build/icon.png",
        "desktop/build/icon.ico",
        "web/assets/brand-mark.png",
    ):
        path = ROOT / relative
        assert path.is_file() and path.stat().st_size > 0


def test_build_script_has_explicit_flavors_and_isolated_outputs():
    text = _read(BUILD_SCRIPT)
    assert '[ValidateSet("cpu", "gpu")]' in text
    assert '$Flavor = "cpu"' in text
    assert '$requiresCuda = $Flavor -eq "gpu"' in text
    assert '"release\\" + $Flavor' in text
    assert "CINESUB_BUILD_FLAVOR" in text
    assert "generate_release_manifest.py" in text
    assert '$collectorArgs["RequireCuda"] = $true' in text
    assert "RequireCuda" in text  # legacy compatibility


def test_release_manifest_hashes_artifacts_and_records_cpu_policy(tmp_path):
    module = _manifest_module()
    output = tmp_path / "output"
    runtime = tmp_path / "runtime"
    output.mkdir()
    (runtime / "python").mkdir(parents=True)
    (runtime / "python" / "python.exe").write_bytes(b"python")
    (runtime / "tools" / "ffmpeg" / "bin").mkdir(parents=True)
    (runtime / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (runtime / "tools" / "ffmpeg" / "bin" / "ffprobe.exe").write_bytes(b"ffprobe")
    artifact = output / "CineSubStudio-0.6.0-windows-x64-cpu-setup.exe"
    artifact.write_bytes(b"installer")
    (output / "CineSubStudio-0.5.0-windows-x64-cpu-setup.exe").write_bytes(b"stale")

    manifest = module.build_manifest(
        output_dir=output,
        runtime_dir=runtime,
        version="0.6.0",
        flavor="cpu",
    )

    assert manifest["build_flavor"] == "cpu"
    assert manifest["components"]["cuda_runtime"] is False
    assert manifest["components"]["nvidia_driver"] is False
    assert manifest["components"]["whisper_models"] is False
    assert [item["name"] for item in manifest["artifacts"]] == [artifact.name]
    assert manifest["artifacts"][0]["sha256"] == "9C0D294C05FC1D88D698034609BB81C0C69196327594E4C69D2915C80FD9850C"


def test_release_manifest_enforces_flavor_cuda_boundary(tmp_path):
    module = _manifest_module()
    output = tmp_path / "output"
    runtime = tmp_path / "runtime"
    output.mkdir()
    runtime.mkdir()
    (output / "setup.exe").write_bytes(b"installer")

    with pytest.raises(RuntimeError, match="without staged CUDA"):
        module.build_manifest(output_dir=output, runtime_dir=runtime, version="0.6.0", flavor="gpu")

    (runtime / "tools" / "cuda").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="unexpectedly contains CUDA"):
        module.build_manifest(output_dir=output, runtime_dir=runtime, version="0.6.0", flavor="cpu")


def test_stage2_ui_has_dynamic_build_identity_and_readiness_flow():
    html = _read(HTML)
    assert 'id="appVersionChip"' in html
    assert 'id="appFlavorChip"' in html
    assert "fetch('/api/app-info')" in html
    assert 'id="readinessChecklist"' in html
    assert "运行环境 → 翻译接口 → 语言配置 → 开始任务" in html
    assert 'id="status-result"' in html
    assert "Subtitle timeline" in html
    assert "v0.2 Preview" not in html


def test_stage2_ui_has_unified_safe_states_without_alerts():
    html = _read(HTML)
    for marker in (
        "function safeUiText(value)",
        "function asyncStateMarkup(kind, title, detail, action)",
        "function renderAsyncState(targetId, kind, title, detail, action)",
        'class="async-state',
        'aria-live="polite"',
        "prefers-reduced-motion",
    ):
        assert marker in html
    assert "alert(" not in html
    assert "sk-m12-secret-should-not-leak" not in html


def test_fonts_are_local_and_licensed():
    html = _read(HTML)
    assert "fonts.googleapis.com" not in html
    assert "/assets/fonts/BarlowCondensed-SemiBold.ttf" in html
    assert "/assets/fonts/NotoSansSC-Variable.ttf" in html
    for name in ("BarlowCondensed-OFL.txt", "NotoSansSC-OFL.txt"):
        assert (ROOT / "web" / "assets" / "fonts" / name).is_file()
