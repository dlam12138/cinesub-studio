from __future__ import annotations

import os
from pathlib import Path

from storage_api import format_bytes


def inspect_input_file(body: dict, *, output_dir: Path, supported_extensions: set[str]) -> dict:
    from subtitle_model import DEFAULT_ASS_STYLE_ID, normalize_subtitle_formats, plan_subtitle_outputs

    path_text = str(body.get("path") or "").strip()
    model = str(body.get("model") or "small").strip() or "small"
    target_language = str(body.get("target_language") or "zh-CN").strip() or "zh-CN"
    translation_mode = str(body.get("translation_mode") or "bilingual").strip()
    subtitle_formats = normalize_subtitle_formats(body.get("subtitle_formats") or ["srt"])
    ass_style_id = str(body.get("ass_style_id") or DEFAULT_ASS_STYLE_ID).strip() or DEFAULT_ASS_STYLE_ID
    mode_tag = "translated" if translation_mode == "translated" else "bilingual"
    if not path_text:
        return {"ok": False, "error": "Enter a local file path.", "path": "", "exists": False, "supported": False}
    try:
        input_path = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return {"ok": False, "error": f"路径无法解析: {exc}", "path": path_text, "exists": False, "supported": False}
    suffix = input_path.suffix.lower()
    exists = input_path.exists()
    is_file = input_path.is_file() if exists else False
    supported = suffix in supported_extensions
    readable = bool(exists and is_file and os.access(input_path, os.R_OK))
    size = 0
    mtime = None
    if exists and is_file:
        try:
            stat = input_path.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            readable = False
    source_output = output_dir / f"{input_path.stem}.{model}.srt"
    translated_output = output_dir / f"{input_path.stem}.{model}.{mode_tag}.{target_language}.srt"
    output_plan = plan_subtitle_outputs(
        output_root=output_dir, stem=input_path.stem, model=model, target_language=target_language,
        translation_mode=mode_tag, formats=subtitle_formats, ass_style_id=ass_style_id,
    )
    warnings: list[str] = []
    if not exists:
        warnings.append("File does not exist. Check that the path is complete.")
    elif not is_file:
        warnings.append("This path is not a file. Single-file processing needs a video or audio file.")
    if exists and is_file and not supported:
        warnings.append(f"Extension {suffix or '(none)'} is not supported.")
    if exists and is_file and not readable:
        warnings.append("The current web process may not have permission to read this file.")
    if source_output.exists():
        warnings.append("The expected source SRT already exists and may be overwritten.")
    if translated_output.exists():
        warnings.append("The expected translated SRT already exists and may be overwritten.")
    return {
        "ok": True, "path": str(input_path), "exists": exists, "is_file": is_file,
        "supported": supported, "readable": readable, "extension": suffix, "bytes": size,
        "display_size": format_bytes(size), "modified_at": mtime, "model": model,
        "target_language": target_language, "translation_mode": mode_tag,
        "source_output": str(source_output.resolve()), "source_output_exists": source_output.exists(),
        "translated_output": str(translated_output.resolve()),
        "translated_output_exists": translated_output.exists(), "subtitle_output_plan": output_plan,
        "warnings": warnings, "ready": bool(exists and is_file and supported and readable),
    }
