from __future__ import annotations

from pathlib import Path
from typing import Callable

from runtime_env import (
    build_diagnostic_summary,
    download_environment_components,
    download_plan,
    import_offline_package,
    runtime_diagnostics,
)


def get_runtime_diagnostics() -> dict:
    try:
        diagnostics = runtime_diagnostics()
    except Exception as exc:
        item = _diagnostic_item(
            "runtime_diagnostics",
            "运行环境诊断",
            "error",
            "读取失败",
            f"运行环境诊断读取失败：{str(exc)[:200]}",
            "请查看 Web 启动终端或 logs/ 中的错误信息；该错误不会触发模型下载或流水线执行。",
            True,
        )
        return {
            "ok": False,
            "error": str(exc)[:200],
            "diagnostic_items": [item],
            "diagnostic_summary": build_diagnostic_summary([item]),
        }
    items = list(diagnostics.get("diagnostic_items") or [])
    items.append(_provider_diagnostic_item())
    items.append(_web_service_diagnostic_item())
    diagnostics["diagnostic_items"] = items
    diagnostics["diagnostic_summary"] = build_diagnostic_summary(items)
    return diagnostics


def get_runtime_download_plan(components: list[str] | None = None) -> dict:
    return download_plan(components or None)


def run_runtime_download(components: list[str], dry_run: bool = True) -> dict:
    return download_environment_components(components or [], dry_run=dry_run)


def import_runtime_package(body: dict) -> dict:
    package_path = str(body.get("path") or body.get("zip_path") or "").strip()
    if not package_path:
        raise ValueError("Missing offline package path.")
    return import_offline_package(package_path)


def import_uploaded_runtime_package(
    *,
    form: dict,
    project_root: Path,
    sanitize_filename: Callable[[str], str],
) -> dict:
    upload = form.get("package")
    if not isinstance(upload, dict) or not upload.get("content"):
        raise ValueError("Upload field 'package' is required.")
    filename = sanitize_filename(str(upload.get("filename") or "offline-package.zip"))
    if not filename.lower().endswith(".zip"):
        raise ValueError("Offline package upload must be a .zip file.")
    target_dir = project_root / ".tmp" / "offline-package-upload"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(upload["content"])
    return import_offline_package(target)


def create_runtime_diagnostic_bundle() -> dict:
    from diagnostic_bundle import create_diagnostic_bundle

    return create_diagnostic_bundle()


def _provider_diagnostic_item() -> dict:
    try:
        from provider_store import get_active_provider, mask_api_key

        provider = get_active_provider()
    except Exception as exc:
        return _diagnostic_item(
            "provider",
            "Provider 配置",
            "warning",
            "读取失败",
            f"Provider 配置读取失败：{str(exc)[:120]}",
            "如需翻译，请检查 config/providers.local.json 是否有效。",
            False,
        )

    if not provider:
        return _diagnostic_item(
            "provider",
            "Provider 配置",
            "not_configured",
            "未配置 active provider",
            "Provider 未配置不会阻止 Web UI 启动；转写和本地质检仍可使用，但翻译任务会失败。",
            "如果需要翻译，请在模型接口中配置 API Key、翻译模型，并设为默认 Provider。",
            False,
        )

    name = str(provider.get("name") or provider.get("id") or "active provider")
    model = str(provider.get("translation_model") or "")
    api_key = str(provider.get("api_key") or "")
    masked = mask_api_key(api_key) if api_key else ""
    if not api_key:
        return _diagnostic_item(
            "provider",
            "Provider 配置",
            "not_configured",
            f"{name} | API Key 未设置",
            "已找到 active Provider，但尚未配置 API Key；Web UI 可启动，实际翻译任务会失败。",
            "请在模型接口中编辑 Provider，并填写 API Key。",
            False,
        )
    if not model:
        return _diagnostic_item(
            "provider",
            "Provider 配置",
            "warning",
            f"{name} | Key {masked} | 模型未设置",
            "API Key 已保存，但翻译模型未设置；Web UI 可启动，实际翻译任务会失败。",
            "请在模型接口中选择或填写翻译模型。",
            False,
        )
    return _diagnostic_item(
        "provider",
        "Provider 配置",
        "ok",
        f"{name} | {model} | Key {masked}",
        "已找到 active Provider，且 API Key 以脱敏形式显示。",
        "无需操作。",
        False,
    )


def _web_service_diagnostic_item() -> dict:
    return _diagnostic_item(
        "web_service",
        "Web 服务",
        "ok",
        "127.0.0.1",
        "本地 Web 服务正在响应当前 diagnostics 请求。",
        "无需操作。",
        False,
    )


def _diagnostic_item(
    item_id: str,
    label: str,
    status: str,
    value: str,
    explanation: str,
    suggestion: str,
    blocking: bool,
) -> dict:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "value": value,
        "explanation": explanation,
        "suggestion": suggestion,
        "blocking": blocking,
    }
