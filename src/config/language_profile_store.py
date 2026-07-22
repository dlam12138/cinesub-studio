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

from encoding_utils import read_json
from runtime_paths import resolve_runtime_paths
from config_recovery import ConfigCorruptError


def _resolve_language_profile_config_path(anchor: Path | str | None = None) -> tuple[Path, Path]:
    paths = resolve_runtime_paths(anchor or Path(__file__).resolve())
    return paths.project_root, paths.config_root / "language_profiles.local.json"


PROJECT_ROOT, CONFIG_PATH = _resolve_language_profile_config_path()
DEFAULT_SUBTITLE_STYLE: dict = {
    "formats": ["srt"],
    "ass_style_id": "clean-cn",
    "enabled": False,
    "note": "ASS output is reserved for a future version.",
}
SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "client_secret",
    "authorization",
    "password",
    "bearer",
)

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
        "asr_mode": "auto",
        "source_language": "auto",
        "target_language": "zh-CN",
        "asr": {
            "whisper_model": "small",
            "whisper_device": "cpu",
            "compute_type": "int8",
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
        "asr_mode": "fixed",
        "source_language": "fr",
        "target_language": "zh-CN",
        "asr": {
            "whisper_model": "small",
            "whisper_device": "cpu",
            "compute_type": "int8",
            "language": "fr",
            "task": "transcribe",
            "vad_filter": True,
            "beam_size": 5,
            "condition_on_previous_text": True,
        },
        "quality": {
            "language_probability_warning": 0.90,
            "language_probability_error": 0.70,
            "max_cps_zh": 10,
            "max_chars_per_line_zh": 20,
            "max_chars_per_subtitle_zh": 40,
        },
        "llm_stages": {
            "proofread_source": True,
            "polish_translation": True,
        },
        "translation_style": (
            "你是一位专业的法语电影字幕翻译师。请将法语字幕翻译成自然、简洁、适合观影的简体中文。"
            "\n\n"
            "=== 翻译原则 ===\n"
            "1. 优先使用双行字幕，每行独立语义完整，避免三行及以上。\n"
            "2. 以意译为主，允许适度压缩。当法语表达冗长时，提取核心含义并精简表达，不追求逐字对应。\n"
            "3. 完整翻译：避免省略关键信息（主语、谓语、核心情感），确保译文完整传达原文含义。\n"
            "4. 根据场景调整语言风格：正式场合（演讲、法庭、新闻）使用书面语；日常对话使用口语化表达。\n"
            "5. 粗俗语言处理：脏话使用中文中同等力度的表达，或根据影片分级适当弱化。\n"
            "6. 标点规范：使用中文全角标点（，。！？），不使用英文半角标点。\n"
            "7. 专有名词：人名、地名使用中文通用译法（如 Jean → 让）。无通用译法的保留法语原文并加注释。\n"
            "8. 文化典故：涉及中国观众不熟悉的法国文化典故时，采用意译或简短注释，不直接使用原文。\n"
            "9. 双关与幽默：优先保留幽默效果，可适当改写以触发中文观众的笑点。若无法保留双关，使用同类型幽默替换。\n"
            "10. 角色语气一致性：每个角色保持稳定的语气风格。粗鲁的角色始终用粗鲁的语气，文雅的角色始终用文雅的语气。\n"
            "\n"
            "=== 硬性约束 ===\n"
            "1. 单行长度不超过20个字符，每行字幕字数限制在约束范围内。\n"
            "2. 字幕阅读节奏：每秒不超过10个字符（CPS），确保观众有足够时间阅读。\n"
            "\n"
            "=== 输出格式 ===\n"
            "- 保持原文的字幕编号和时间轴\n"
            "- 使用双行字幕，每行独立语义完整\n"
            "- 不添加任何解释性注释，只输出翻译后的文本"
        ),
    },
    {
        "id": "generic-european-film",
        "name": "欧洲语种通用",
        "description": "欧洲语种通用影片。涵盖西班牙语、意大利语、德语、葡萄牙语、荷兰语、瑞典语、波兰语、捷克语等。启用原文校对和译文润色。",
        "builtin": True,
        "asr_mode": "auto",
        "source_language": "auto",
        "target_language": "zh-CN",
        "asr": {
            "whisper_model": "small",
            "whisper_device": "cpu",
            "compute_type": "int8",
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
            data = read_json(CONFIG_PATH)
            data.setdefault("version", 1)
            data.setdefault("active", "auto-detect")
            data.setdefault("profiles", [])
            _cache = data
            _cache_mtime = CONFIG_PATH.stat().st_mtime
            return _cache
        except (OSError, json.JSONDecodeError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            _cache = None
            _cache_mtime = 0.0
            raise ConfigCorruptError("language_profiles") from exc


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


def _is_secret_field(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SECRET_FIELD_MARKERS)


def remove_secret_fields(value):
    """Return a copy with provider/API secret-looking fields removed."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if _is_secret_field(key):
                continue
            clean[key] = remove_secret_fields(item)
        return clean
    if isinstance(value, list):
        return [remove_secret_fields(item) for item in value]
    return value


def normalize_glossary(raw) -> list[dict]:
    """Normalize profile glossary rows and drop incomplete entries."""
    if not isinstance(raw, list):
        return []

    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        target = str(entry.get("target") or "").strip()
        note = str(entry.get("note") or "").strip()
        if not source or not target:
            continue
        key = (source, target)
        if key in seen:
            continue
        seen.add(key)
        normalized = {"source": source, "target": target, "note": note}
        for key in ("aliases", "asr_variants"):
            values = entry.get(key)
            if isinstance(values, list):
                normalized[key] = [
                    str(item).strip() for item in values if str(item).strip()
                ][:32]
        for key in ("type", "gender", "role"):
            value = str(entry.get(key) or "").strip()
            if value:
                normalized[key] = value
        result.append(normalized)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 查询 API
# ═══════════════════════════════════════════════════════════════════════════

def _merge_with_defaults(local_profiles: list[dict]) -> list[dict]:
    """合并内置默认和本地配置。本地优先（相同 id 则覆盖内置）。"""
    merged: dict[str, dict] = {}
    for bp in BUILTIN_PROFILES:
        merged[bp["id"]] = _with_profile_defaults(deepcopy(bp))
    for lp in local_profiles:
        pid = lp.get("id", "")
        if not pid:
            continue
        copy = remove_secret_fields(deepcopy(lp))
        copy["builtin"] = merged[pid]["builtin"] if pid in merged else False
        merged[pid] = _with_profile_defaults(copy)
    return sorted(merged.values(), key=lambda p: (not p.get("builtin", False), p.get("id", "")))


def _with_subtitle_style(profile: dict) -> dict:
    style = deepcopy(DEFAULT_SUBTITLE_STYLE)
    incoming = profile.get("subtitle_style")
    if isinstance(incoming, dict):
        style.update(incoming)
    formats = style.get("formats")
    if isinstance(formats, str):
        formats = [item.strip() for item in formats.split(",") if item.strip()]
    if not isinstance(formats, list) or not formats:
        formats = ["srt"]
    if "srt" not in formats:
        formats.insert(0, "srt")
    style["formats"] = formats
    style["enabled"] = False
    profile["subtitle_style"] = style
    return profile


def _with_profile_defaults(profile: dict) -> dict:
    profile = remove_secret_fields(profile)
    profile = _with_subtitle_style(profile)
    profile["glossary"] = normalize_glossary(profile.get("glossary", []))
    profile["asr"] = _normalize_asr_config(profile.get("asr"))
    source_language = str(profile.get("source_language") or "").strip()
    legacy_language = profile["asr"].get("language") or (
        source_language if source_language and source_language != "auto" else None
    )
    from asr_runtime import normalize_asr_request

    mode, language = normalize_asr_request(
        profile.get("asr_mode") or ("fixed" if legacy_language else "auto"),
        legacy_language,
        reject_conflict=False,
    )
    profile["asr_mode"] = mode
    profile["source_language"] = language if mode == "fixed" else "auto"
    profile["asr"]["language"] = language
    profile["translation_reliability"] = _normalize_translation_reliability(
        profile.get("translation_reliability")
    )
    profile["translation_strategy"] = _normalize_translation_strategy(
        profile.get("translation_strategy")
    )
    return profile


def _normalize_asr_config(value: object) -> dict:
    data = deepcopy(value) if isinstance(value, dict) else {}
    data.pop("recognizer", None)
    data.pop("aligner", None)
    from asr_runtime import normalize_asr_retry_mode

    if "word_timestamps" in data:
        data["word_timestamps"] = data.get("word_timestamps") is True
    if "resegment_subtitles" in data:
        data["resegment_subtitles"] = data.get("resegment_subtitles") is True
    if "asr_retry_mode" in data:
        data["asr_retry_mode"] = normalize_asr_retry_mode(data.get("asr_retry_mode"))
    if "asr_hotword_prompt" in data:
        data["asr_hotword_prompt"] = str(data.get("asr_hotword_prompt") or "").strip()[:512]
    return data


def _normalize_translation_reliability(value: object) -> dict:
    from translation_reliability import normalize_reliability_config

    return normalize_reliability_config(value)


def _normalize_translation_strategy(value: object) -> dict:
    from translation_strategy import normalize_translation_strategy

    return normalize_translation_strategy(value)


def list_language_profiles() -> list[dict]:
    """返回所有 language profiles（合并默认 + 本地）。"""
    try:
        data = _load_raw()
    except ConfigCorruptError:
        return _merge_with_defaults([])
    local = data.get("profiles", [])
    return _merge_with_defaults(local)


def get_language_profile(profile_id: str) -> dict | None:
    """获取单个 language profile。先查本地，再查内置。"""
    try:
        data = _load_raw()
    except ConfigCorruptError:
        data = dict(DEFAULT_EMPTY_CONFIG)
    for p in data.get("profiles", []):
        if p.get("id") == profile_id:
            return _with_profile_defaults(deepcopy(p))
    for bp in BUILTIN_PROFILES:
        if bp["id"] == profile_id:
            return _with_profile_defaults(deepcopy(bp))
    return None


def get_active_language_profile() -> dict:
    """获取当前 active language profile。默认返回 auto-detect。"""
    try:
        data = _load_raw()
    except ConfigCorruptError:
        data = dict(DEFAULT_EMPTY_CONFIG)
    active_id = data.get("active", "auto-detect") or "auto-detect"
    profile = get_language_profile(active_id)
    return profile if profile else _with_profile_defaults(deepcopy(BUILTIN_PROFILES[0]))


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
    profile_data = remove_secret_fields(profile_data)
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
        "asr_mode": profile_data.get("asr_mode"),
        "source_language": profile_data.get("source_language", "auto"),
        "target_language": profile_data.get("target_language", "zh-CN"),
        "asr": {
            "whisper_model": profile_data.get("asr", {}).get("whisper_model", "small"),
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
        "translation_reliability": _normalize_translation_reliability(
            profile_data.get("translation_reliability")
        ),
        "translation_strategy": _normalize_translation_strategy(
            profile_data.get("translation_strategy")
        ),
        "translation_style": (profile_data.get("translation_style") or "").strip(),
        "subtitle_style": _with_subtitle_style({"subtitle_style": profile_data.get("subtitle_style", {})})["subtitle_style"],
        "glossary": normalize_glossary(profile_data.get("glossary", [])),
    }
    for key in ("word_timestamps", "resegment_subtitles", "asr_retry_mode", "asr_hotword_prompt"):
        if key in profile_data.get("asr", {}):
            new_p["asr"][key] = _normalize_asr_config(profile_data.get("asr", {})).get(key)

    from asr_runtime import normalize_asr_request

    src_lang = new_p["source_language"]
    requested_mode = new_p["asr_mode"] or (
        "fixed" if src_lang and src_lang != "auto" else "auto"
    )
    mode, language = normalize_asr_request(
        requested_mode,
        new_p["asr"].get("language") or src_lang,
        reject_conflict=False,
    )
    new_p["asr_mode"] = mode
    new_p["source_language"] = language if mode == "fixed" else "auto"
    new_p["asr"]["language"] = language
    if mode == "fixed":
        # 严格阈值
        new_p["quality"]["language_probability_warning"] = profile_data.get("quality", {}).get("language_probability_warning", 0.90)
        new_p["quality"]["language_probability_error"] = profile_data.get("quality", {}).get("language_probability_error", 0.70)

    data = _load_raw()
    profiles = data.get("profiles", [])
    found = False
    for i, p in enumerate(profiles):
        if p.get("id") == pid:
            profiles[i] = remove_secret_fields(new_p)
            found = True
            break
    if not found:
        profiles.append(remove_secret_fields(new_p))
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
    try:
        _normalize_asr_config(data.get("asr"))
        from asr_runtime import normalize_asr_request
        source_language = data.get("source_language")
        normalize_asr_request(
            data.get("asr_mode")
            or ("fixed" if source_language and source_language != "auto" else "auto"),
            data.get("asr", {}).get("language") or source_language,
            reject_conflict=False,
        )
    except ValueError as exc:
        errors.append(str(exc))
    try:
        _normalize_translation_reliability(data.get("translation_reliability"))
    except ValueError as exc:
        errors.append(str(exc))
    try:
        _normalize_translation_strategy(data.get("translation_strategy"))
    except ValueError as exc:
        errors.append(str(exc))
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
        profile = _with_profile_defaults(deepcopy(BUILTIN_PROFILES[0]))

    return {
        "asr_mode": profile.get("asr_mode", "auto"),
        "source_language": profile.get("source_language", "auto"),
        "target_language": profile.get("target_language", "zh-CN"),
        "asr": _normalize_asr_config(profile.get("asr")),
        "quality": profile.get("quality", {}),
        "translation_style": profile.get("translation_style", ""),
        "subtitle_style": _with_subtitle_style(deepcopy(profile)).get("subtitle_style", deepcopy(DEFAULT_SUBTITLE_STYLE)),
        "glossary": normalize_glossary(profile.get("glossary", [])),
        "llm_stages": profile.get("llm_stages", {}),
        "translation_reliability": _normalize_translation_reliability(
            profile.get("translation_reliability")
        ),
        "translation_strategy": _normalize_translation_strategy(
            profile.get("translation_strategy")
        ),
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
