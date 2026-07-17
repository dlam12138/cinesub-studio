from __future__ import annotations

from subtitle_model import DEFAULT_ASS_STYLE_ID, normalize_subtitle_formats


def _first(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def resolve_cli_config(args, raw_argv: list[str]) -> tuple[dict, list[str]]:
    """Resolve CLI > Profile > Provider > default precedence without side effects."""
    explicit = lambda *flags: any(flag in raw_argv for flag in flags)
    messages: list[str] = []
    provider: dict = {}
    if args.provider is not None or not args.no_translate:
        try:
            from provider_store import resolve_provider_config

            provider = resolve_provider_config(args.provider)
            if provider:
                messages.append(f"  [Provider] using config: {args.provider or '(active)'}")
        except Exception as exc:
            messages.append(f"  [Provider] load failed: {exc}")
    profile: dict = {}
    try:
        from language_profile_store import resolve_language_profile_config

        profile = resolve_language_profile_config(args.language_profile or None)
        if profile:
            messages.append(
                f"  [LangProfile] using config: {profile.get('profile_id', '?')} "
                f"({profile.get('profile_name', '?')})"
            )
    except Exception as exc:
        messages.append(f"  [LangProfile] load failed: {exc}")

    asr = profile.get("asr", {})
    from subtitle_translate import build_effective_translation_prompt

    style = profile.get("subtitle_style", {})
    profile_strategy = profile.get("asr_strategy", {})
    profile_reliability = profile.get("translation_reliability", {})
    strategy = {
        "mode": _first(args.asr_experiment_mode, profile_strategy.get("mode"), "off"),
        "candidate_id": _first(args.asr_candidate_id, profile_strategy.get("candidate_id"), ""),
    }
    from asr_strategy import validate_strategy_config

    strategy = validate_strategy_config(strategy, model=_first(args.model, asr.get("whisper_model"), "large-v3"))
    from translation_reliability import normalize_reliability_config

    reliability = normalize_reliability_config({
        "mode": _first(
            args.translation_reliability_mode
            if explicit("--translation-reliability-mode") else None,
            profile_reliability.get("mode"),
            "off",
        ),
        "max_extra_requests": _first(
            args.translation_max_extra_requests
            if explicit("--translation-max-extra-requests") else None,
            profile_reliability.get("max_extra_requests"),
            12,
        ),
    })
    profile_info = {
        "profile_id": profile.get("profile_id", ""),
        "profile_name": profile.get("profile_name", ""),
        "source_language": profile.get("source_language", "auto"),
        "quality_thresholds": profile.get("quality", {}),
        "translation_style": profile.get("translation_style", ""),
        "glossary": profile.get("glossary", []),
        "subtitle_style": style,
        "llm_stages": profile.get("llm_stages", {}),
        "asr_strategy": strategy,
        "translation_reliability": reliability,
    }
    values = {
        "api_provider": _first(args.api_provider, provider.get("api_provider"), "openai-compatible"),
        "api_base": _first(args.api_base, provider.get("api_base"), ""),
        "api_key": _first(args.api_key, provider.get("api_key"), ""),
        "llm_model": _first(args.llm_model, provider.get("llm_model"), ""),
        "translation_quality_model": _first(
            args.translation_quality_model,
            provider.get("translation_quality_model"),
            args.llm_model,
            provider.get("llm_model"),
            "",
        ),
        "model": _first(args.model if explicit("--model") else None, asr.get("whisper_model"), "large-v3"),
        "device": _first(args.device if explicit("--device") else None, asr.get("whisper_device"), "auto"),
        "compute_type": _first(args.compute_type if explicit("--compute-type") else None, asr.get("compute_type")),
        "language": _first(args.language if explicit("--language") else None, asr.get("language")),
        "vad_filter": False if explicit("--no-vad") else asr.get("vad_filter", True),
        "beam_size": args.beam_size if explicit("--beam-size") else asr.get("beam_size", 5),
        "target_language": _first(
            args.target_language if explicit("--target-language") else None,
            profile.get("target_language"), "zh-CN",
        ),
        "translation_prompt": build_effective_translation_prompt(
            style_prompt=profile.get("translation_style", ""),
            custom_prompt=args.translation_prompt,
            glossary=profile.get("glossary", []),
        ),
        "subtitle_formats": normalize_subtitle_formats(
            args.subtitle_formats if explicit("--subtitle-formats") else style.get("formats", ["srt"])
        ),
        "ass_style_id": _first(
            args.ass_style_id if explicit("--ass-style-id") else None,
            style.get("ass_style_id"), DEFAULT_ASS_STYLE_ID,
        ),
        "subtitle_style": style,
        "profile_info": profile_info,
        "asr_strategy": strategy,
        "translation_reliability": reliability,
    }
    return values, messages
