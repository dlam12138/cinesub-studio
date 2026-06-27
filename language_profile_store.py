"""
Language Profile Store — 语言配置档案管理

管理 config/language_profiles.local.json 中的多语言/多影片类型配置。
内置 3 个默认 profile（auto-detect / fr-film / generic-european-film），
本地配置可覆盖或新增。

职责边界：
  Provider 管 API Key / API Base / LLM 模型
  Language Profile 管语言 / ASR 参数 / 质检阈值 / 翻译风格
  不要把 API Key 写进 Language Profile
"""

from __future__ import annotations

import json, os, tempfile, threading, time
from copy import deepcopy
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "language_profiles.local.json"

_cache: dict | None = None
_cache_lock = threading.Lock()
_cache_mtime: float = 0.0

# ═══════════════════════════════════════════════════════════════════════════
# 内置默认 Profiles
# ═══════════════════════════════════════════════════════════════════════════

BUILTIN_PROFILES: list[dict] = [
    {
        "id": "auto-detect",
        "name": "自动识别语言",
        "description": "未知语言影片默认模式。由 Whisper 自动检测源语言，根据 language_probability 给出 warning/error。",
        "builtin": True,
        "source_language": "auto",
        "target_language": "zh-CN",
        "asr": {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "compute_type": "float16",
            "language": None,
            "task": "transcribe",
            "vad_filter": True,
            "beam_size": 5,
            "condition_on_previous_text": True,
        },
        "quality": {
            "language_probability_warning": 0.85,
            "language_probability_error": 0.60,
            "max_cps_zh": 8,
            "max_chars_per_line_zh": 18,
            "max_chars_per_subtitle_zh": 36,
        },
        "llm_stages": {
            "proofread_source": False,
            "polish_translation": False,
        },
        "translation_style": (
            "将原文字幕翻译为自然、简洁、适合观影的简体中文字幕。"
            "保持字幕编号和时间轴不变。不要解释，不要注释。专有名词前后一致。"
        ),
    },
    {
        "id": "fr-film",
        "name": "法语电影",
        "description": "法语电影、法语访谈、法语剧情片。强制法语识别，启用原文校对和译文润色。",
        "builtin": True,
        "source_language": "fr",
        "target_language": "zh-CN",
        "asr": {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "compute_type": "float16",
            "language": "fr",
            "task": "transcribe",
            "vad_filter": True,
            "beam_size": 5,
            "condition_on_previous_text": True,
        },
        "quality": {
            "language_probability_warning": 0.90,
            "language_probability_error": 0.70,
            "max_cps_zh": 8,
            "max_chars_per_line_zh": 18,
            "max_chars_per_subtitle_zh": 36,
        },
        "llm_stages": {
            "proofread_source": True,
            "polish_translation": True,
        },
        "translation_style": (
            "法语电影中文字幕风格。翻译自然、简洁、适合观影。"
            "保留法语人名地名的原文写法，必要时括号标注中文译名。不要过度本地化。"
            "保持字幕编号和时间轴不变。不要解释，不要注释。"
        ),
    },
    {
        "id": "generic-european-film",
        "name": "欧洲语种通用",
        "description": "欧洲语种通用影片。涵盖西班牙语、意大利语、德语、葡萄牙语、荷兰语、瑞典语、波兰语、捷克语等。启用原文校对和译文润色。",
        "builtin": True,
        "source_language": "auto",
        "target_language": "zh-CN",
        "asr": {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "compute_type": "float16",
            "language": None,
            "task": "transcribe",
            "vad_filter": True,
            "beam_size": 5,
            "condition_on_previous_text": True,
        },
        "quality": {
            "language_probability_warning": 0.80,
            "language_probability_error": 0.55,
            "max_cps_zh": 8,
            "max_chars_per_line_zh": 18,
            "max_chars_per_subtitle_zh": 36,
        },
        "llm_stages": {
            "proofread_source": True,
            "polish_translation": True,
        },
        "translation_style": (
            "欧洲电影中文字幕风格。保留人名地名的原文写法，翻译自然简洁。"
            "避免过度本地化，保留欧洲语言的表达习惯。"
            "保持字幕编号和时间轴不变。不要解释，不要注释。"
        ),
    },
]

DEFAULT_EMPTY_CONFIG: dict = {
    "version": 1,
    "active": "auto-detect",
    "profiles": [],
}


# ═══════════════════════════════════════════════════════════════════════════
# 底层读写
# ═══════════════════════════════════════════════════════════════════════════

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
            data.setdefault("version", 1)
            data.setdefault("active", "auto-detect")
            data.setdefault("profiles", [])
            _cache = data
            _cache_mtime = CONFIG_PATH.stat().st_mtime
            return _cache
        except (OSError, json.JSONDecodeError, ValueError):
            _cache = dict(DEFAULT_EMPTY_CONFIG)
            _cache_mtime = 0.0
            return _cache


def _save_raw(data: dict) -> None:
    global _cache, _cache_mtime
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="lang_profiles_", dir=str(CONFIG_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    with _cache_lock:
        _cache = data
        _cache_mtime = CONFIG_PATH.stat().st_mtime


def _clear_cache() -> None:
    global _cache, _cache_mtime
    with _cache_lock:
        _cache = None
        _cache_mtime = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 查询 API
# ═══════════════════════════════════════════════════════════════════════════

def _merge_with_defaults(local_profiles: list[dict]) -> list[dict]:
    """合并内置默认和本地配置。本地优先（相同 id 则覆盖内置）。"""
    merged: dict[str, dict] = {}
    for bp in BUILTIN_PROFILES:
        merged[bp["id"]] = deepcopy(bp)
    for lp in local_profiles:
        pid = lp.get("id", "")
        if not pid:
            continue
        copy = deepcopy(lp)
        copy["builtin"] = merged[pid]["builtin"] if pid in merged else False
        merged[pid] = copy
    return sorted(merged.values(), key=lambda p: (not p.get("builtin", False), p.get("id", "")))


def list_language_profiles() -> list[dict]:
    """返回所有 language profiles（合并默认 + 本地）。"""
    data = _load_raw()
    local = data.get("profiles", [])
    return _merge_with_defaults(local)


def get_language_profile(profile_id: str) -> dict | None:
    """获取单个 language profile。先查本地，再查内置。"""
    data = _load_raw()
    for p in data.get("profiles", []):
        if p.get("id") == profile_id:
            return deepcopy(p)
    for bp in BUILTIN_PROFILES:
        if bp["id"] == profile_id:
            return deepcopy(bp)
    return None


def get_active_language_profile() -> dict:
    """获取当前 active language profile。默认返回 auto-detect。"""
    data = _load_raw()
    active_id = data.get("active", "auto-detect") or "auto-detect"
    profile = get_language_profile(active_id)
    return profile if profile else deepcopy(BUILTIN_PROFILES[0])


def set_active_language_profile(profile_id: str) -> None:
    """设为当前默认语言配置。"""
    profile = get_language_profile(profile_id)
    if not profile:
        raise ValueError(f"Language Profile '{profile_id}' 不存在")
    data = _load_raw()
    data["active"] = profile_id
    _save_raw(data)


# ═══════════════════════════════════════════════════════════════════════════
# 增删改
# ═══════════════════════════════════════════════════════════════════════════

def upsert_language_profile(profile_data: dict) -> dict:
    """新增或更新本地 language profile。"""
    pid = (profile_data.get("id") or "").strip()
    if not pid:
        raise ValueError("Profile ID 不能为空")
    if not (profile_data.get("name") or "").strip():
        raise ValueError("Profile 名称不能为空")

    # 规范化
    new_p = {
        "id": pid,
        "name": (profile_data.get("name") or "").strip(),
        "description": (profile_data.get("description") or "").strip(),
        "source_language": profile_data.get("source_language", "auto"),
        "target_language": profile_data.get("target_language", "zh-CN"),
        "asr": {
            "whisper_model": profile_data.get("asr", {}).get("whisper_model", "large-v3"),
            "whisper_device": profile_data.get("asr", {}).get("whisper_device", "cpu"),
            "compute_type": profile_data.get("asr", {}).get("compute_type", "int8"),
            "language": profile_data.get("asr", {}).get("language"),
            "task": profile_data.get("asr", {}).get("task", "transcribe"),
            "vad_filter": profile_data.get("asr", {}).get("vad_filter", True) is not False,
            "beam_size": profile_data.get("asr", {}).get("beam_size", 5),
            "condition_on_previous_text": profile_data.get("asr", {}).get("condition_on_previous_text", True) is not False,
        },
        "quality": {
            "language_probability_warning": profile_data.get("quality", {}).get("language_probability_warning", 0.85),
            "language_probability_error": profile_data.get("quality", {}).get("language_probability_error", 0.60),
            "max_cps_zh": profile_data.get("quality", {}).get("max_cps_zh", 8),
            "max_chars_per_line_zh": profile_data.get("quality", {}).get("max_chars_per_line_zh", 18),
            "max_chars_per_subtitle_zh": profile_data.get("quality", {}).get("max_chars_per_subtitle_zh", 36),
        },
        "llm_stages": {
            "proofread_source": profile_data.get("llm_stages", {}).get("proofread_source", False) is True,
            "polish_translation": profile_data.get("llm_stages", {}).get("polish_translation", False) is True,
        },
        "translation_style": (profile_data.get("translation_style") or "").strip(),
    }

    # 如果 source_language 不是 auto，同步设置 asr.language
    src_lang = new_p["source_language"]
    if src_lang and src_lang != "auto":
        new_p["asr"]["language"] = src_lang
        # 严格阈值
        new_p["quality"]["language_probability_warning"] = profile_data.get("quality", {}).get("language_probability_warning", 0.90)
        new_p["quality"]["language_probability_error"] = profile_data.get("quality", {}).get("language_probability_error", 0.70)

    data = _load_raw()
    profiles = data.get("profiles", [])
    found = False
    for i, p in enumerate(profiles):
        if p.get("id") == pid:
            profiles[i] = new_p
            found = True
            break
    if not found:
        profiles.append(new_p)
        if len(profiles) == 1 and not data.get("active"):
            data["active"] = pid
    data["profiles"] = profiles
    _save_raw(data)
    return new_p


def delete_language_profile(profile_id: str) -> None:
    """删除本地 language profile。若删除 active，回退到 auto-detect。"""
    # 禁止删除内置 profile（只能删除本地覆盖版本）
    for bp in BUILTIN_PROFILES:
        if bp["id"] == profile_id:
            data = _load_raw()
            profiles = [p for p in data.get("profiles", []) if p.get("id") != profile_id]
            data["profiles"] = profiles
            if data.get("active") == profile_id:
                data["active"] = "auto-detect"
            _save_raw(data)
            return  # 已删除本地覆盖，保留内置

    data = _load_raw()
    profiles = data.get("profiles", [])
    new_list = [p for p in profiles if p.get("id") != profile_id]
    if len(new_list) == len(profiles):
        raise ValueError(f"Language Profile '{profile_id}' 不存在或为内置只读")
    data["profiles"] = new_list
    if data.get("active") == profile_id:
        data["active"] = "auto-detect"
    _save_raw(data)


# ═══════════════════════════════════════════════════════════════════════════
# 验证
# ═══════════════════════════════════════════════════════════════════════════

def validate_language_profile(data: dict) -> list[str]:
    """校验 language profile 数据，返回错误列表。"""
    errors = []
    if not (data.get("id") or "").strip():
        errors.append("Profile ID 不能为空")
    if not (data.get("name") or "").strip():
        errors.append("Profile 名称不能为空")
    target = data.get("target_language", "")
    if not target:
        errors.append("目标语言不能为空")
    return errors


# ═══════════════════════════════════════════════════════════════════════════
# batch_worker / CLI 集成
# ═══════════════════════════════════════════════════════════════════════════

def resolve_language_profile_config(profile_id: str | None = None) -> dict:
    """解析 language profile 配置，返回可用于 BatchConfig / transcribe 的字段字典。

    优先级：指定 profile_id > active profile > auto-detect

    Returns:
        {
            "source_language": "fr" | "auto",
            "target_language": "zh-CN",
            "asr": {...},           # ASR 参数
            "quality": {...},       # 质检阈值
            "translation_style": "",# 翻译风格 prompt
            "llm_stages": {...},    # 校对/润色开关
            "profile_id": "fr-film",
            "profile_name": "法语电影",
        }
    """
    profile = None
    if profile_id:
        profile = get_language_profile(profile_id)
        if not profile:
            raise ValueError(f"Language Profile '{profile_id}' 不存在")
    else:
        profile = get_active_language_profile()

    if not profile:
        profile = deepcopy(BUILTIN_PROFILES[0])

    return {
        "source_language": profile.get("source_language", "auto"),
        "target_language": profile.get("target_language", "zh-CN"),
        "asr": profile.get("asr", {}),
        "quality": profile.get("quality", {}),
        "translation_style": profile.get("translation_style", ""),
        "llm_stages": profile.get("llm_stages", {}),
        "profile_id": profile.get("id", "auto-detect"),
        "profile_name": profile.get("name", "自动识别语言"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 兼容别名
# ═══════════════════════════════════════════════════════════════════════════

load_language_profiles = list_language_profiles
save_language_profiles = _save_raw
merge_default_profiles_with_local = _merge_with_defaults
atomic_write_json = _save_raw
