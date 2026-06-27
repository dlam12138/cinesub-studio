"""
Provider Store — 本地模型接口配置管理

管理 config/providers.local.json 中的多个 API Provider 配置。
支持 CRUD、设为默认、测试连接、CLI 集成。

安全特性：
- 原子写入（temp → replace）
- 列表接口默认脱敏 API Key
- 日志不输出完整 Key
"""

from __future__ import annotations

import json
import os
import threading
import tempfile
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "providers.local.json"

# 内存缓存 + 线程锁
_cache: dict | None = None
_cache_lock = threading.Lock()
_cache_mtime: float = 0.0

DEFAULT_EMPTY_CONFIG: dict = {
    "version": 1,
    "active": "",
    "providers": [],
}


# ── 底层读写 ────────────────────────────────────────────────────────────

def _load_raw() -> dict:
    """从磁盘加载配置（带文件修改时间缓存）。"""
    global _cache, _cache_mtime
    with _cache_lock:
        if _cache is not None and CONFIG_PATH.exists():
            try:
                mtime = CONFIG_PATH.stat().st_mtime
                if mtime == _cache_mtime:
                    return _cache
            except OSError:
                pass

        if not CONFIG_PATH.exists():
            _cache = dict(DEFAULT_EMPTY_CONFIG)
            _cache_mtime = 0.0
            return _cache

        try:
            raw = CONFIG_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or "providers" not in data:
                raise ValueError("Invalid providers config format")
            # 确保必要字段存在
            data.setdefault("version", 1)
            data.setdefault("active", "")
            data.setdefault("providers", [])
            _cache = data
            _cache_mtime = CONFIG_PATH.stat().st_mtime
            return _cache
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"无法读取 Provider 配置文件: {exc}")


def _save_raw(data: dict) -> None:
    """原子写入配置到磁盘（先写临时文件，再 replace 到目标）。"""
    global _cache, _cache_mtime
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # 写入临时文件
    fd, tmp_path = tempfile.mkstemp(
        suffix=".json",
        prefix="providers_",
        dir=str(CONFIG_DIR),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        # 原子替换
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    with _cache_lock:
        _cache = data
        _cache_mtime = CONFIG_PATH.stat().st_mtime


def _clear_cache() -> None:
    """重置内存缓存（主要用于测试）。"""
    global _cache, _cache_mtime
    with _cache_lock:
        _cache = None
        _cache_mtime = 0.0


# ── 脱敏 ─────────────────────────────────────────────────────────────────

def mask_api_key(key: str) -> str:
    """脱敏 API Key 为 'sk-...abcd' 格式。"""
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "***"
    return key[:3] + "..." + key[-4:]


def _mask_provider(provider: dict) -> dict:
    """返回脱敏后的 provider 副本。"""
    p = dict(provider)
    if p.get("api_key"):
        p["api_key_masked"] = mask_api_key(p["api_key"])
    p.pop("api_key", None)
    return p


# ── 查询接口 ─────────────────────────────────────────────────────────────

def load_providers() -> dict:
    """返回完整的 providers 配置 dict（含完整 API Key，调用方负责安全）。"""
    return _load_raw()


def list_providers(mask_secret: bool = True) -> list[dict]:
    """列出所有 provider，默认脱敏 API Key。"""
    data = _load_raw()
    providers = data.get("providers", [])
    if mask_secret:
        return [_mask_provider(p) for p in providers]
    return list(providers)


def get_provider(provider_id: str) -> dict | None:
    """根据 ID 获取单个 provider（含完整 Key）。"""
    data = _load_raw()
    for p in data.get("providers", []):
        if p.get("id") == provider_id:
            return dict(p)
    return None


def get_active_provider() -> dict | None:
    """获取当前默认 provider（含完整 Key），没有则返回 None。"""
    data = _load_raw()
    active_id = data.get("active", "")
    if not active_id:
        return None
    return get_provider(active_id)


def set_active_provider(provider_id: str) -> None:
    """设置为当前默认 provider。"""
    data = _load_raw()
    # 验证 provider 存在且启用
    found = False
    for p in data.get("providers", []):
        if p.get("id") == provider_id:
            if not p.get("enabled", True):
                raise ValueError(f"Provider '{provider_id}' 已被禁用，无法设为默认")
            found = True
            break
    if not found:
        raise ValueError(f"Provider '{provider_id}' 不存在")
    data["active"] = provider_id
    _save_raw(data)


# ── 增删改 ───────────────────────────────────────────────────────────────

def upsert_provider(provider_data: dict) -> dict:
    """新增或更新 provider。id 已存在则更新，否则新增。

    校验规则：
    - id 不能为空
    - name 不能为空
    - api_base 不能为空
    - 至少提供 translation_model 或 chat_model
    """
    provider_id = (provider_data.get("id") or "").strip()
    if not provider_id:
        raise ValueError("Provider ID 不能为空")
    if not (provider_data.get("name") or "").strip():
        raise ValueError("Provider 名称不能为空")
    if not (provider_data.get("api_base") or "").strip():
        raise ValueError("API Base 不能为空")
    if not (provider_data.get("translation_model") or provider_data.get("chat_model") or "").strip():
        raise ValueError("翻译模型或聊天模型至少填写一个")

    data = _load_raw()
    providers = data.get("providers", [])

    # 规范化字段
    new_provider = {
        "id": provider_id,
        "name": (provider_data.get("name") or "").strip(),
        "protocol": provider_data.get("protocol", "openai-compatible"),
        "api_base": (provider_data.get("api_base") or "").strip(),
        "api_key": provider_data.get("api_key", ""),
        "chat_model": (provider_data.get("chat_model") or "").strip(),
        "translation_model": (provider_data.get("translation_model") or "").strip(),
        "whisper_model": provider_data.get("whisper_model", "large-v3"),
        "whisper_device": provider_data.get("whisper_device", "cpu"),
        "enabled": provider_data.get("enabled", True) is not False,
        "notes": (provider_data.get("notes") or "").strip(),
    }

    # 更新时：如果 api_key 为空，保留旧 key
    if not new_provider["api_key"]:
        existing = get_provider(provider_id)
        if existing:
            new_provider["api_key"] = existing.get("api_key", "")

    # 更新或新增
    found = False
    for i, p in enumerate(providers):
        if p.get("id") == provider_id:
            providers[i] = new_provider
            found = True
            break
    if not found:
        providers.append(new_provider)

    # 如果是第一个 provider，自动设为 active
    if len(providers) == 1 and not data.get("active"):
        data["active"] = provider_id

    data["providers"] = providers
    _save_raw(data)
    return new_provider


def delete_provider(provider_id: str) -> None:
    """删除 provider。如果删除的是 active provider，自动清空 active。"""
    data = _load_raw()
    providers = data.get("providers", [])
    new_list = [p for p in providers if p.get("id") != provider_id]

    if len(new_list) == len(providers):
        raise ValueError(f"Provider '{provider_id}' 不存在")

    data["providers"] = new_list

    # 如果删除的是 active provider，清空 active
    if data.get("active") == provider_id:
        # 尝试切换到第一个启用的 provider
        first_enabled = next((p for p in new_list if p.get("enabled", True)), None)
        data["active"] = first_enabled["id"] if first_enabled else ""

    _save_raw(data)


# ── CLI 集成 ─────────────────────────────────────────────────────────────

def resolve_provider_config(provider_id: str | None = None) -> dict:
    """解析 provider 配置，返回可用于 BatchConfig 的字段字典。

    优先级：指定的 provider_id > active provider

    Returns:
        {
            "api_provider": "openai-compatible",
            "api_base": "https://...",
            "api_key": "sk-...",
            "llm_model": "gpt-4o",
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
        }
        如果没有配置则返回空 dict
    """
    provider = None
    if provider_id:
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Provider '{provider_id}' 不存在")
    else:
        provider = get_active_provider()

    if not provider:
        return {}

    # 翻译模型优先，fallback 到聊天模型
    llm_model = provider.get("translation_model") or provider.get("chat_model") or ""

    return {
        "api_provider": provider.get("protocol", "openai-compatible"),
        "api_base": provider.get("api_base", ""),
        "api_key": provider.get("api_key", ""),
        "llm_model": llm_model,
        "whisper_model": provider.get("whisper_model", "large-v3"),
        "whisper_device": provider.get("whisper_device", "cpu"),
    }


# ── 测试连接 ─────────────────────────────────────────────────────────────

def test_provider_connection(provider_id: str) -> dict:
    """测试指定 provider 的连接。

    Returns:
        {"ok": True/False, "latency_ms": 123, "model": "gpt-4o", "error": ""}
    """
    provider = get_provider(provider_id)
    if not provider:
        return {"ok": False, "error": f"Provider '{provider_id}' 不存在", "latency_ms": 0, "model": ""}

    api_base = (provider.get("api_base") or "").strip()
    api_key = provider.get("api_key", "")
    model = provider.get("translation_model") or provider.get("chat_model") or ""

    if not api_base:
        return {"ok": False, "error": "API Base 未设置", "latency_ms": 0, "model": ""}
    if not api_key:
        return {"ok": False, "error": "API Key 未设置", "latency_ms": 0, "model": ""}
    if not model:
        return {"ok": False, "error": "模型未设置", "latency_ms": 0, "model": ""}

    url = api_base.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a test endpoint."},
            {"role": "user", "content": "Return OK."},
        ],
        "max_tokens": 5,
    }, ensure_ascii=False)

    import urllib.request
    import urllib.error

    start = time.perf_counter()
    try:
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8")
            latency_ms = round((time.perf_counter() - start) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "model": model, "error": ""}

    except urllib.error.HTTPError as e:
        latency_ms = round((time.perf_counter() - start) * 1000)
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        # 不要在错误消息中包含 API Key
        return {"ok": False, "latency_ms": latency_ms, "model": model,
                "error": f"HTTP {e.code}: {error_body}"}

    except urllib.error.URLError as e:
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "model": model,
                "error": f"连接失败: {e.reason}"}

    except Exception as e:
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "model": model,
                "error": str(e)[:300]}
