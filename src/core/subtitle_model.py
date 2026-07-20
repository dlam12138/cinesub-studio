from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_SUBTITLE_FORMATS: dict[str, str] = {
    "srt": "enabled",
    "ass": "reserved",
}

DEFAULT_SUBTITLE_FORMATS = ["srt"]
DEFAULT_ASS_STYLE_ID = "clean-cn"
ASS_RESERVED_MESSAGE = "ASS output is reserved for a future version; no .ass file was generated."


@dataclass(slots=True)
class SubtitleCue:
    index: int
    start: float | None = None
    end: float | None = None
    time_line: str = ""
    source_text: str = ""
    translated_text: str = ""
    style: str = ""
    speaker: str = ""
    notes: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubtitleDocument:
    cues: list[SubtitleCue] = field(default_factory=list)
    source_language: str = ""
    target_language: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubtitleRenderOptions:
    formats: list[str] = field(default_factory=lambda: list(DEFAULT_SUBTITLE_FORMATS))
    ass_style_id: str = DEFAULT_ASS_STYLE_ID
    subtitle_style: dict[str, Any] = field(default_factory=dict)


def normalize_subtitle_formats(value: Any) -> list[str]:
    if value is None or value == "":
        return list(DEFAULT_SUBTITLE_FORMATS)
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    formats: list[str] = []
    for item in raw_items:
        text = str(item).strip().lower()
        if not text:
            continue
        if text not in SUPPORTED_SUBTITLE_FORMATS:
            raise ValueError(f"Unsupported subtitle format: {text}")
        if text not in formats:
            formats.append(text)

    if "srt" not in formats:
        formats.insert(0, "srt")
    return formats or list(DEFAULT_SUBTITLE_FORMATS)


def ass_reserved_requested(formats: Any) -> bool:
    return "ass" in normalize_subtitle_formats(formats)


def subtitle_format_status(formats: Any, ass_style_id: str = DEFAULT_ASS_STYLE_ID) -> dict[str, Any]:
    normalized = normalize_subtitle_formats(formats)
    reserved: list[dict[str, str]] = []
    if "ass" in normalized:
        reserved.append({
            "format": "ass",
            "style_id": ass_style_id or DEFAULT_ASS_STYLE_ID,
            "message": ASS_RESERVED_MESSAGE,
        })
    return {
        "formats": normalized,
        "enabled": [fmt for fmt in normalized if SUPPORTED_SUBTITLE_FORMATS.get(fmt) == "enabled"],
        "reserved": reserved,
        "ass_enabled": False,
    }


def plan_subtitle_outputs(
    *,
    output_root: Path,
    stem: str,
    model: str,
    target_language: str = "zh-CN",
    translation_mode: str = "bilingual",
    formats: Any = None,
    ass_style_id: str = DEFAULT_ASS_STYLE_ID,
) -> dict[str, Any]:
    output_root = Path(output_root)
    mode_tag = "bilingual" if translation_mode == "bilingual" else "translated"
    status = subtitle_format_status(formats, ass_style_id=ass_style_id)
    return {
        **status,
        "srt": {
            "source": str((output_root / f"{stem}.{model}.srt").resolve()),
            "translated": str((output_root / f"{stem}.{model}.{mode_tag}.{target_language}.srt").resolve()),
        },
        "ass": {
            "reserved_dir": str((output_root / "ass").resolve()),
            "reserved_path": str((output_root / "ass" / f"{stem}.{model}.{mode_tag}.{target_language}.ass").resolve()),
            "message": ASS_RESERVED_MESSAGE,
        },
    }
