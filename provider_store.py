"""
Provider Store — 本地翻译接口配置管理（DeepSeek 优先）

管理 config/providers.local.json 中的 Provider 配置。
Provider 只负责翻译 API，不再包含 Whisper/ASR 参数。

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

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "providers.local.json"

_cache: dict | None = None
_cache_lock = threading.Lock()
_cache_mtime: float = 0.0

DEFAULT_EMPTY_CONFIG: dict = {
    "version": 2,
    "active": "",
    "providers": [],
}

# ── 迁移：旧字段 → 新字段 ──────────────────────────────────────────────

def _migrate_provider(p: dict) -> dict:
    """兼容旧格式 provider，迁移过时字段。"""
    p = dict(p)
    # chat_model → translation_model
    if not p.get("translation_model") and p.get("chat_model"):
        p["translation_model"] = p["chat_model"]
    # 移除 ASR 字段（已迁移到 Language Profile）
    for key in ("whisper_model", "whisper_device", "chat_model"):
        p.pop(key, None)
    # 确保新字段存在
    p.setdefault("template_id", "")
    p.setdefault("enabled", True)
    p.setdefault("translation_model", "")
    return p


# ── 底层读写 ────────────────────────────────────────────────────────────

def _load_raw() -> dict:
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
            data.setdefault("version", 2)
            data.setdefault("active", "")
            data.setdefault("providers", [])
            # 迁移所有旧 provider
            data["providers"] = [_migrate_provider(p) for p in data["providers"]]
            _cache = data
            _cache_mtime = CONFIG_PATH.stat().st_mtime
            return _cache
        except (OSError, json.JSONDecodeError, ValueError):
            _cache = dict(DEFAULT_EMPTY_CONFIG)
            _cache_mtime = 0.0
            return _cache


def _save_raw(data: dict) -> None:
    global _cache, _cache_mtime
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # 保存前清理旧字段
    clean = dict(data)
    clean["providers"] = [_migrate_provider(p) for p in clean.get("providers", [])]
    payload = json.dumps(clean, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="providers_", dir=str(CONFIG_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    with _cache_lock:
        _cache = clean
        _cache_mtime = CONFIG_PATH.stat().st_mtime


# ── 脱敏 ─────────────────────────────────────────────────────────────────

def mask_api_key(key: str) -> str:
    if not key: return ""
    if len(key) <= 8: return key[:2] + "***"
    return key[:3] + "..." + key[-4:]


def _mask_provider(provider: dict) -> dict:
    p = dict(provider)
    if p.get("api_key"):
        p["api_key_masked"] = mask_api_key(p["api_key"])
    p.pop("api_key", None)
    return p


# ── 查询接口 ─────────────────────────────────────────────────────────────

def load_providers() -> dict:
    return _load_raw()


def list_providers(mask_secret: bool = True) -> list[dict]:
    data = _load_raw()
    providers = data.get("providers", [])
    if mask_secret:
        return [_mask_provider(p) for p in providers]
    return list(providers)


def get_provider(provider_id: str) -> dict | None:
    data = _load_raw()
    for p in data.get("providers", []):
        if p.get("id") == provider_id:
            return dict(p)
    return None


def get_active_provider() -> dict | None:
    data = _load_raw()
    active_id = data.get("active", "")
    if not active_id: return None
    return get_provider(active_id)


def set_active_provider(provider_id: str) -> None:
    data = _load_raw()
    for p in data.get("providers", []):
        if p.get("id") == provider_id:
            if not p.get("enabled", True):
                raise ValueError(f"Provider '{provider_id}' 已被禁用")
            data["active"] = provider_id
            _save_raw(data)
            return
    raise ValueError(f"Provider '{provider_id}' 不存在")


# ── 增删改（简化版） ────────────────────────────────────────────────────

PROVIDER_FIELDS = ["id", "name", "template_id", "protocol", "api_base", "api_key", "translation_model", "enabled", "notes"]


def _auto_id(template_id: str = "") -> str:
    """自动生成唯一 ID。"""
    base = (template_id or "custom") + "-main"
    data = _load_raw()
    existing = {p["id"] for p in data.get("providers", [])}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def upsert_provider(provider_data: dict) -> dict:
    """新增或更新 provider。支持模板模式（template_id 自动填充协议和 api_base）。"""
    provider_id = (provider_data.get("id") or "").strip()

    # ── 模板模式（新增） ──
    template_id = (provider_data.get("template_id") or "").strip()
    if not provider_id:
        provider_id = _auto_id(template_id)
    is_new = not any(p["id"] == provider_id for p in _load_raw().get("providers", []))

    name = (provider_data.get("name") or "").strip()
    api_base = (provider_data.get("api_base") or "").strip()
    api_key = provider_data.get("api_key", "")
    model = (provider_data.get("model") or provider_data.get("translation_model") or "").strip()
    protocol = (provider_data.get("protocol") or "openai-compatible").strip()

    # 模板模式：自动填充 api_base / protocol / model
    if template_id and not api_base:
        for tpl in PROVIDER_TEMPLATES:
            if tpl["id"] == template_id:
                api_base = tpl.get("api_base", "")
                protocol = tpl.get("protocol", protocol)
                if not model and tpl.get("models"):
                    # 找默认模型
                    default_model = next((m["id"] for m in tpl["models"] if m.get("default")), tpl["models"][0]["id"])
                    model = default_model
                break

    # 校验
    if not name: raise ValueError("Provider 名称不能为空")
    if not (model or (not is_new)): raise ValueError("模型不能为空")
    if not api_base: raise ValueError("API Base 不能为空")

    new_provider = {
        "id": provider_id,
        "name": name,
        "template_id": template_id,
        "protocol": protocol,
        "api_base": api_base,
        "api_key": api_key,
        "translation_model": model,
        "enabled": provider_data.get("enabled", True) is not False,
        "notes": (provider_data.get("notes") or "").strip(),
    }

    # 编辑时：api_key 为空则保留旧 key
    if not new_provider["api_key"] and not is_new:
        existing = get_provider(provider_id)
        if existing: new_provider["api_key"] = existing.get("api_key", "")

    # 保存
    data = _load_raw()
    providers = data.get("providers", [])
    found = False
    for i, p in enumerate(providers):
        if p.get("id") == provider_id:
            providers[i] = new_provider
            found = True
            break
    if not found:
        providers.append(new_provider)
        if len(providers) == 1 and not data.get("active"):
            data["active"] = provider_id

    data["providers"] = providers
    _save_raw(data)
    return new_provider


def delete_provider(provider_id: str) -> None:
    data = _load_raw()
    providers = data.get("providers", [])
    new_list = [p for p in providers if p.get("id") != provider_id]
    if len(new_list) == len(providers):
        raise ValueError(f"Provider '{provider_id}' 不存在")
    data["providers"] = new_list
    if data.get("active") == provider_id:
        first = next((p for p in new_list if p.get("enabled", True)), None)
        data["active"] = first["id"] if first else ""
    _save_raw(data)


# ── CLI 集成（简化版，只返回翻译 API 字段） ────────────────────────────

def resolve_provider_config(provider_id: str | None = None) -> dict:
    """解析 provider 配置，只返回翻译 API 所需字段。不再包含 ASR 参数。"""
    provider = None
    if provider_id:
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Provider '{provider_id}' 不存在")
    else:
        provider = get_active_provider()
    if not provider:
        return {}
    return {
        "api_provider": provider.get("protocol", "openai-compatible"),
        "api_base": provider.get("api_base", ""),
        "api_key": provider.get("api_key", ""),
        "llm_model": provider.get("translation_model", ""),
    }


# ── Provider Templates（内置模板） ───────────────────────────────────────

PROVIDER_TEMPLATES = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "protocol": "openai-compatible",
        "api_base": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "default": True,
             "description": "推荐作为字幕批量翻译默认模型"},
            {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "default": False,
             "description": "适合疑难片段、润色或更高质量翻译"},
        ],
        "deprecated_models": [
            {"id": "deepseek-chat", "replacement": "deepseek-v4-flash",
             "deprecated_after": "2026-07-24 23:59 Asia/Shanghai"},
            {"id": "deepseek-reasoner", "replacement": "deepseek-v4-pro",
             "deprecated_after": "2026-07-24 23:59 Asia/Shanghai"},
        ],
    },
    {
        "id": "custom-openai-compatible",
        "name": "自定义 OpenAI-compatible",
        "protocol": "openai-compatible",
        "api_base": "",
        "models": [],
    },
]


def get_provider_templates() -> list[dict]:
    """返回内置 Provider 模板列表（不含 API Key）。"""
    return PROVIDER_TEMPLATES


# ── 测试连接 ─────────────────────────────────────────────────────────────

def test_provider_connection(provider_id: str) -> dict:
    provider = get_provider(provider_id)
    if not provider:
        return {"ok": False, "error": f"Provider '{provider_id}' 不存在", "latency_ms": 0, "model": ""}
    api_base = (provider.get("api_base") or "").strip()
    api_key = provider.get("api_key", "")
    model = provider.get("translation_model", "")
    if not api_base: return {"ok": False, "error": "API Base 未设置", "latency_ms": 0, "model": ""}
    if not api_key: return {"ok": False, "error": "API Key 未设置", "latency_ms": 0, "model": ""}
    if not model: return {"ok": False, "error": "模型未设置", "latency_ms": 0, "model": ""}

    url = api_base.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a test endpoint."},
            {"role": "user", "content": "Return OK."},
        ],
        "stream": False,
        "max_tokens": 5,
    }, ensure_ascii=False)

    import urllib.request, urllib.error
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            latency = round((time.perf_counter() - start) * 1000)
            return {"ok": True, "latency_ms": latency, "model": model, "error": ""}
    except urllib.error.HTTPError as e:
        latency = round((time.perf_counter() - start) * 1000)
        err = e.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "latency_ms": latency, "model": model, "error": f"HTTP {e.code}: {err}"}
    except urllib.error.URLError as e:
        latency = round((time.perf_counter() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "model": model, "error": f"连接失败: {e.reason}"}
    except Exception as e:
        return {"ok": False, "latency_ms": 0, "model": model, "error": str(e)[:300]}
