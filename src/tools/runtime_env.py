from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ffmpeg_locator import find_ffmpeg_info


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
CUDA_DIR = TOOLS_DIR / "cuda"
PYTHON_DIR = TOOLS_DIR / "python"
WHEELHOUSE_DIR = TOOLS_DIR / "wheelhouse"
MODEL_DIR = PROJECT_ROOT / "models"
CACHE_DIR = PROJECT_ROOT / ".cache"
TMP_DIR = PROJECT_ROOT / ".tmp"
OUTPUT_DIR = PROJECT_ROOT / "output"

ALLOWED_OFFLINE_ROOTS = (
    Path("tools/python"),
    Path("tools/wheelhouse"),
    Path("tools/ffmpeg"),
    Path("tools/cuda"),
    Path("models"),
)

DOWNLOAD_COMPONENTS: dict[str, dict[str, Any]] = {
    "python": {
        "label": "Portable Python 3.12",
        "target": "tools/python/",
        "size": "约 30-80 MB",
        "status": "manual",
        "note": "建议通过离线环境包导入；安装脚本会优先使用 tools/python/python.exe。",
    },
    "wheelhouse": {
        "label": "Python 离线依赖 wheelhouse",
        "target": "tools/wheelhouse/",
        "size": "约 300 MB-2 GB，取决于平台和 CUDA/CPU wheel",
        "status": "manual",
        "note": "建议在有网络的机器生成后随离线包导入。",
    },
    "ffmpeg": {
        "label": "FFmpeg Windows 二进制",
        "target": "tools/ffmpeg/bin/",
        "size": "约 100-150 MB 下载，解压后约 300 MB",
        "status": "downloadable",
        "note": "可由项目内 download_ffmpeg.py 下载，不修改系统 PATH。",
    },
    "cuda": {
        "label": "CUDA/cuDNN 运行时 DLL",
        "target": "tools/cuda/",
        "size": "约 1.5-2 GB",
        "status": "manual",
        "note": "体积大且需匹配 faster-whisper/ctranslate2，建议通过离线包导入。",
    },
    "models": {
        "label": "Whisper 模型缓存",
        "target": "models/ 和 .cache/huggingface/",
        "size": "small 约 500 MB，large-v3 约 3 GB",
        "status": "manual",
        "note": "首次识别可由 faster-whisper 下载；离线使用建议随离线包导入。",
    },
}

SUMMARY_TEMPLATES = {
    "ok": ("环境可用", "当前运行环境检查通过。"),
    "warning": ("环境基本可用，但存在建议项", "部分项目需要注意，但不一定阻断使用。"),
    "not_configured": ("部分功能尚未配置", "基础功能可用，但某些功能需要配置后才能使用。"),
    "error": ("存在阻断问题", "检测到会阻断核心功能的问题，请先处理错误项。"),
}


def add_project_cuda_to_process() -> bool:
    """Expose project CUDA DLLs to the current process only."""
    if not CUDA_DIR.exists():
        return False
    cuda_text = str(CUDA_DIR)
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(cuda_text)
        except OSError:
            pass
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if cuda_text not in parts:
        os.environ["PATH"] = cuda_text + os.pathsep + os.environ.get("PATH", "")
    return True


def add_project_cuda_to_env(env: dict[str, str]) -> bool:
    if not CUDA_DIR.exists():
        return False
    cuda_text = str(CUDA_DIR)
    parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if cuda_text not in parts:
        env["PATH"] = cuda_text + (os.pathsep + env.get("PATH", "") if env.get("PATH") else "")
    return True


def choose_device(requested_device: str) -> tuple[str, list[str]]:
    """Resolve auto/CUDA/CPU with CUDA-first semantics."""
    device = (requested_device or "auto").strip().lower()
    warnings: list[str] = []
    if device == "auto":
        diag = runtime_diagnostics()
        if diag.get("cuda_ready"):
            return "cuda", warnings
        reason = "; ".join(diag.get("cuda_messages") or ["CUDA is not ready"])
        warnings.append(f"CUDA unavailable, falling back to CPU: {reason}")
        return "cpu", warnings
    if device == "cuda":
        diag = runtime_diagnostics()
        if not diag.get("cuda_ready"):
            reason = "; ".join(diag.get("cuda_messages") or ["CUDA is not ready"])
            raise RuntimeError(
                "CUDA was requested but the project CUDA runtime is not ready. "
                f"{reason}. Use device=auto/cpu or import/download the CUDA environment package."
            )
    if device not in {"cpu", "cuda"}:
        return "cpu", [f"Unknown device '{requested_device}', using CPU."]
    return device, warnings


def default_compute_type(device: str, compute_type: str | None) -> str:
    if compute_type:
        return compute_type
    return "float16" if device == "cuda" else "int8"


def build_diagnostic_summary(items: list[dict[str, Any]]) -> dict[str, str]:
    if any(item.get("status") == "error" and item.get("blocking") for item in items):
        status = "error"
    elif any(item.get("status") == "warning" for item in items):
        status = "warning"
    elif any(item.get("status") == "not_configured" for item in items):
        status = "not_configured"
    else:
        status = "ok"
    title, message = SUMMARY_TEMPLATES[status]
    return {"status": status, "title": title, "message": message}


def runtime_diagnostics() -> dict[str, Any]:
    add_project_cuda_to_process()
    executable = Path(sys.executable).resolve()
    project_venv = (PROJECT_ROOT / ".venv").resolve()
    in_project_venv = str(executable).lower().startswith(str(project_venv).lower())
    python_prefix = Path(sys.prefix).resolve()
    python_base_prefix = Path(sys.base_prefix).resolve()
    portable_python = PYTHON_DIR / ("python.exe" if sys.platform == "win32" else "python")
    venv_base_executable = _venv_base_executable(project_venv)
    portable_root_text = str(PYTHON_DIR.resolve()).lower()
    base_prefix_text = str(python_base_prefix).lower()
    base_executable_text = str(venv_base_executable).lower() if venv_base_executable else ""
    portable_base = (
        base_prefix_text.startswith(portable_root_text)
        or base_executable_text.startswith(portable_root_text)
        or str(executable).lower().startswith(portable_root_text)
    )
    if portable_base:
        python_source = "project-portable-python"
    elif in_project_venv:
        python_source = "project-venv-system-base"
    else:
        python_source = "system-python"
    version_info = sys.version_info
    python_supported = (3, 9) <= (version_info.major, version_info.minor) <= (3, 12)

    ffmpeg_info = find_ffmpeg_info(PROJECT_ROOT)
    ffmpeg_path = ffmpeg_info["path"]
    cublas_ok = (CUDA_DIR / "cublas64_12.dll").exists()
    cudnn_files = sorted(CUDA_DIR.glob("cudnn*_9.dll")) if CUDA_DIR.exists() else []
    cudnn_ok = bool(cudnn_files)
    nvidia = _nvidia_driver_info()

    faster_whisper_ok, faster_whisper_error = _module_status("faster_whisper")
    ctranslate2_ok, ctranslate2_error = _module_status("ctranslate2")

    cuda_messages: list[str] = []
    if not CUDA_DIR.exists():
        cuda_messages.append("tools/cuda/ not found")
    if not cublas_ok:
        cuda_messages.append("missing cublas64_12.dll")
    if not cudnn_ok:
        cuda_messages.append("missing cudnn*_9.dll")
    if not nvidia["ok"]:
        cuda_messages.append(nvidia["message"])
    if not faster_whisper_ok:
        cuda_messages.append("faster-whisper is not importable")
    if not ctranslate2_ok:
        cuda_messages.append("ctranslate2 is not importable")

    known_models = _known_models()
    cuda_ready = not cuda_messages
    diagnostic_items = _runtime_diagnostic_items(
        python_supported=python_supported,
        python_version=sys.version.split()[0],
        python_path=str(executable),
        in_project_venv=in_project_venv,
        project_venv=str(project_venv),
        python_source=python_source,
        portable_python_exists=portable_python.exists(),
        ffmpeg_info=ffmpeg_info,
        known_models=known_models,
        cuda_ready=cuda_ready,
        cuda_messages=cuda_messages,
        recommended_device="cuda" if cuda_ready else "cpu",
        recommended_compute_type="float16" if cuda_ready else "int8",
    )

    return {
        "ok": True,
        "project_root": str(PROJECT_ROOT),
        "python": str(executable),
        "python_version": sys.version.split()[0],
        "python_supported": python_supported,
        "python_source": python_source,
        "python_prefix": str(python_prefix),
        "python_base_prefix": str(python_base_prefix),
        "python_base_executable": str(venv_base_executable) if venv_base_executable else "",
        "project_venv": str(project_venv),
        "in_project_venv": in_project_venv,
        "portable_python": str(portable_python),
        "portable_python_exists": portable_python.exists(),
        "portable_python_recommended": not portable_base,
        "faster_whisper_ok": faster_whisper_ok,
        "faster_whisper_error": faster_whisper_error,
        "ctranslate2_ok": ctranslate2_ok,
        "ctranslate2_error": ctranslate2_error,
        "ffmpeg_path": ffmpeg_path or "",
        "ffmpeg_ok": bool(ffmpeg_path),
        "ffmpeg_source": ffmpeg_info["source"],
        "ffmpeg_source_label": ffmpeg_info["source_label"],
        "model_dir": str(MODEL_DIR),
        "known_models": known_models,
        "hf_home": str(CACHE_DIR / "huggingface"),
        "output_dir": str(OUTPUT_DIR),
        "wheelhouse_dir": str(WHEELHOUSE_DIR),
        "wheelhouse_ok": WHEELHOUSE_DIR.exists() and any(WHEELHOUSE_DIR.glob("*.whl")),
        "cuda_runtime_dir": str(CUDA_DIR),
        "cuda_runtime_ok": bool(cublas_ok and cudnn_ok),
        "cuda_ready": cuda_ready,
        "cuda_messages": cuda_messages,
        "cublas_ok": cublas_ok,
        "cudnn_ok": cudnn_ok,
        "cudnn_files": [p.name for p in cudnn_files],
        "nvidia_driver_ok": nvidia["ok"],
        "nvidia_driver": nvidia["message"],
        "recommended_device": "cuda" if cuda_ready else "cpu",
        "recommended_compute_type": "float16" if cuda_ready else "int8",
        "offline_package": {
            "allowed_roots": [str(p).replace("\\", "/") for p in ALLOWED_OFFLINE_ROOTS],
            "import_target": str(PROJECT_ROOT),
        },
        "download_components": download_plan(),
        "diagnostic_summary": build_diagnostic_summary(diagnostic_items),
        "diagnostic_items": diagnostic_items,
    }


def _runtime_diagnostic_items(
    *,
    python_supported: bool,
    python_version: str,
    python_path: str,
    in_project_venv: bool,
    project_venv: str,
    python_source: str,
    portable_python_exists: bool,
    ffmpeg_info: dict[str, Any],
    known_models: list[str],
    cuda_ready: bool,
    cuda_messages: list[str],
    recommended_device: str,
    recommended_compute_type: str,
) -> list[dict[str, Any]]:
    version_parts = sys.version_info
    if python_supported:
        python_status = "ok"
        python_explanation = "当前 Python 版本在推荐支持范围内。"
        python_suggestion = "无需操作。"
        python_blocking = False
    elif (version_parts.major, version_parts.minor) == (3, 13):
        python_status = "warning"
        python_explanation = "当前 Python 不在推荐支持范围内，但基础自检可作为实测参考。"
        python_suggestion = "推荐使用 Python 3.11 或 3.12；如当前功能正常，可继续使用。"
        python_blocking = False
    else:
        python_status = "warning"
        python_explanation = "当前 Python 不在推荐支持范围内，部分 AI/音频依赖可能不兼容。"
        python_suggestion = "推荐重建为 Python 3.11 或 3.12 的项目 .venv。"
        python_blocking = False

    items = [
        _diagnostic_item(
            "python",
            "Python 版本",
            python_status,
            f"{python_version} | {python_path}",
            python_explanation,
            python_suggestion,
            python_blocking,
        ),
        _diagnostic_item(
            "venv",
            "虚拟环境",
            "ok" if in_project_venv else "warning",
            project_venv if in_project_venv else python_source,
            ".venv 已启用。" if in_project_venv else "当前进程未运行在项目 .venv 中。",
            (
                "无需操作。"
                if in_project_venv
                else "建议通过 start_web.ps1 或 .venv\\Scripts\\python.exe 启动。"
            ),
            False,
        ),
        _diagnostic_item(
            "ffmpeg",
            "FFmpeg",
            "ok" if ffmpeg_info["ok"] else "error",
            _ffmpeg_value(ffmpeg_info),
            (
                f"已找到可用 FFmpeg，来源：{ffmpeg_info['source_label']}。"
                if ffmpeg_info["ok"]
                else "未找到 FFmpeg，转写前无法抽取音频。"
            ),
            "无需操作。" if ffmpeg_info["ok"] else "请导入离线包或下载 FFmpeg 到 tools/ffmpeg/bin/。",
            not ffmpeg_info["ok"],
        ),
        _directory_item(
            "output_dir",
            "输出目录",
            OUTPUT_DIR,
            missing_status="ok",
            missing_explanation="output/ 尚不存在，但项目目录可写时会在运行时创建。",
            missing_suggestion="无需操作；如果写入失败，请检查项目目录权限。",
            blocking=True,
        ),
        _model_cache_item(known_models),
        _diagnostic_item(
            "cuda",
            "CUDA / CPU",
            "ok" if cuda_ready else "warning",
            (
                f"CUDA 就绪，推荐 {recommended_device} + {recommended_compute_type}"
                if cuda_ready
                else f"CUDA 未就绪，当前建议 {recommended_device} + {recommended_compute_type}"
            ),
            (
                "CUDA 运行时、NVIDIA 驱动和依赖均可用。"
                if cuda_ready
                else "CUDA 当前不可用，但 CPU 模式仍可运行，只是速度较慢。"
            ),
            "无需操作。" if cuda_ready else _cuda_suggestion(cuda_messages),
            False,
        ),
    ]
    if portable_python_exists:
        items.insert(
            2,
            _diagnostic_item(
                "portable_python",
                "项目内置 Python",
                "ok",
                str(PYTHON_DIR / ("python.exe" if sys.platform == "win32" else "python")),
                "已发现项目内置 portable Python。",
                "后续重建 .venv 时可优先使用它。",
                False,
            ),
        )
    return items


def _diagnostic_item(
    item_id: str,
    label: str,
    status: str,
    value: str,
    explanation: str,
    suggestion: str,
    blocking: bool,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "value": value,
        "explanation": explanation,
        "suggestion": suggestion,
        "blocking": blocking,
    }


def _ffmpeg_value(ffmpeg_info: dict[str, Any]) -> str:
    if not ffmpeg_info["ok"]:
        return ffmpeg_info["source_label"]
    return f"{ffmpeg_info['source_label']} | {ffmpeg_info['path']}"


def _directory_item(
    item_id: str,
    label: str,
    path: Path,
    *,
    missing_status: str,
    missing_explanation: str,
    missing_suggestion: str,
    blocking: bool,
) -> dict[str, Any]:
    if path.exists():
        if path.is_dir() and _can_write_in(path):
            return _diagnostic_item(
                item_id,
                label,
                "ok",
                str(path),
                f"{label}存在且可写。",
                "无需操作。",
                False,
            )
        return _diagnostic_item(
            item_id,
            label,
            "error",
            str(path),
            f"{label}不可写或不是目录。",
            "请检查目录权限，或确认该路径没有被同名文件占用。",
            blocking,
        )

    if _can_create_missing_path(path):
        return _diagnostic_item(
            item_id,
            label,
            missing_status,
            str(path),
            missing_explanation,
            missing_suggestion,
            False,
        )
    return _diagnostic_item(
        item_id,
        label,
        "error",
        str(path),
        f"{label}不存在，且父目录不可写或不存在。",
        "请检查项目目录权限，确保运行时可以创建需要的目录。",
        blocking,
    )


def _model_cache_item(known_models: list[str]) -> dict[str, Any]:
    paths = f"{MODEL_DIR} | {CACHE_DIR / 'huggingface'}"
    model_probe = _directory_probe(MODEL_DIR)
    hf_probe = _directory_probe(CACHE_DIR / "huggingface")
    if model_probe == "error" or hf_probe == "error":
        return _diagnostic_item(
            "model_cache",
            "模型/缓存目录",
            "error",
            paths,
            "模型或 Hugging Face 缓存目录不可写，首次下载或离线模型导入可能失败。",
            "请检查 models/ 和 .cache/huggingface/ 的权限。",
            False,
        )
    if known_models:
        return _diagnostic_item(
            "model_cache",
            "模型/缓存目录",
            "ok",
            ", ".join(known_models),
            "已在项目内模型目录发现可用模型缓存。",
            "无需操作。",
            False,
        )
    return _diagnostic_item(
        "model_cache",
        "模型/缓存目录",
        "not_configured",
        paths,
        "尚未发现已缓存的 Whisper 模型；首次识别可能需要下载或导入离线模型。",
        "如需离线使用，请把模型随离线包导入到 models/ 或 .cache/huggingface/。",
        False,
    )


def _directory_probe(path: Path) -> str:
    if path.exists():
        return "ok" if path.is_dir() and _can_write_in(path) else "error"
    if _can_create_missing_path(path):
        return "missing_but_creatable"
    return "error"


def _can_create_missing_path(path: Path) -> bool:
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.exists() and parent.is_dir() and _can_write_in(parent)


def _can_write_in(path: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(prefix=".cinesub-write-test-", dir=path, delete=True):
            return True
    except OSError:
        return False


def _cuda_suggestion(cuda_messages: list[str]) -> str:
    if cuda_messages:
        reason = "; ".join(cuda_messages)
        return f"如需 GPU 加速，请检查 NVIDIA 驱动、tools/cuda/ 和依赖。当前原因：{reason}"
    return "如需 GPU 加速，请检查 NVIDIA 驱动、tools/cuda/ 和依赖。"


def _venv_base_executable(project_venv: Path) -> Path | None:
    cfg = project_venv / "pyvenv.cfg"
    if not cfg.exists():
        return None
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().lower() == "executable":
                text = value.strip()
                return Path(text).resolve() if text else None
    except OSError:
        return None
    return None


def download_plan(components: list[str] | None = None) -> dict[str, Any]:
    selected = components or list(DOWNLOAD_COMPONENTS)
    items = []
    for name in selected:
        item = DOWNLOAD_COMPONENTS.get(name)
        if not item:
            continue
        items.append({"id": name, **item})
    return {
        "ok": True,
        "dry_run_only_for_large_components": True,
        "items": items,
    }


def download_environment_components(components: list[str], dry_run: bool = True) -> dict[str, Any]:
    components = components or list(DOWNLOAD_COMPONENTS)
    plan = download_plan(components)
    results: list[dict[str, Any]] = []
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan, "results": results}

    for component in components:
        if component == "ffmpeg":
            from download_ffmpeg import main as download_ffmpeg_main

            code = download_ffmpeg_main()
            results.append({"component": component, "ok": code == 0, "returncode": code})
        elif component in DOWNLOAD_COMPONENTS:
            results.append({
                "component": component,
                "ok": False,
                "message": "This large component must be imported from an offline package in this version.",
            })
        else:
            results.append({"component": component, "ok": False, "message": "Unknown component"})
    return {"ok": all(item.get("ok") for item in results), "dry_run": False, "plan": plan, "results": results}


def import_offline_package(zip_path: Path | str) -> dict[str, Any]:
    package = Path(zip_path).expanduser().resolve()
    if not package.exists() or not package.is_file():
        raise ValueError(f"Offline package not found: {package}")
    if package.suffix.lower() != ".zip":
        raise ValueError("Offline package must be a .zip file.")

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    skipped: list[str] = []
    manifest: dict[str, Any] = {}
    total_bytes = 0

    with zipfile.ZipFile(package, "r") as archive:
        infos = archive.infolist()
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"Unsafe path in offline package: {name}")
            if name == "cinesub-offline-manifest.json":
                try:
                    manifest = json.loads(archive.read(info).decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Invalid offline manifest: {exc}") from exc
                continue
            if not _is_allowed_offline_path(rel):
                skipped.append(name)
                continue
            target = (PROJECT_ROOT / rel).resolve()
            if not target.is_relative_to(PROJECT_ROOT.resolve()):
                raise ValueError(f"Package path escapes project root: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            extracted.append(name)
            total_bytes += info.file_size

    return {
        "ok": True,
        "package": str(package),
        "manifest": manifest,
        "extracted_count": len(extracted),
        "skipped_count": len(skipped),
        "extracted_bytes": total_bytes,
        "extracted": extracted[:200],
        "skipped": skipped[:200],
        "diagnostics": runtime_diagnostics(),
    }


def _is_allowed_offline_path(path: Path) -> bool:
    return any(path == root or path.is_relative_to(root) for root in ALLOWED_OFFLINE_ROOTS)


def _module_status(name: str) -> tuple[bool, str]:
    if importlib.util.find_spec(name) is None:
        return False, "module not found"
    try:
        __import__(name)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _nvidia_driver_info() -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"ok": False, "message": "nvidia-smi not found"}
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return {"ok": False, "message": f"nvidia-smi failed: {exc}"}
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "nvidia-smi returned non-zero").strip()
        return {"ok": False, "message": msg[:300]}
    line = (result.stdout or "").strip().splitlines()
    return {"ok": bool(line), "message": line[0] if line else "no NVIDIA GPU reported"}


def _known_models() -> list[str]:
    known: list[str] = []
    if MODEL_DIR.exists():
        for path in sorted(MODEL_DIR.glob("models--*--*")):
            if path.is_dir():
                known.append(path.name.replace("models--", "").replace("--", "/"))
    return known


def main() -> int:
    parser = argparse.ArgumentParser(description="CineSub runtime environment helper.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("diagnostics")

    plan_parser = sub.add_parser("download-plan")
    plan_parser.add_argument("components", nargs="*")

    download_parser = sub.add_parser("download")
    download_parser.add_argument("components", nargs="*")
    download_parser.add_argument("--execute", action="store_true")

    import_parser = sub.add_parser("import-package")
    import_parser.add_argument("zip_path")

    args = parser.parse_args()
    if args.command == "diagnostics":
        print(json.dumps(runtime_diagnostics(), ensure_ascii=False, indent=2))
    elif args.command == "download-plan":
        print(json.dumps(download_plan(args.components), ensure_ascii=False, indent=2))
    elif args.command == "download":
        components = args.components or list(DOWNLOAD_COMPONENTS)
        print(json.dumps(download_environment_components(components, dry_run=not args.execute), ensure_ascii=False, indent=2))
    elif args.command == "import-package":
        print(json.dumps(import_offline_package(args.zip_path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
