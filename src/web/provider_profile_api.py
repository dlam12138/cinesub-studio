from __future__ import annotations


def _provider_payload(provider: dict | None) -> dict | None:
    from provider_store import sanitize_provider

    return sanitize_provider(provider)


def list_provider_payload() -> dict:
    from provider_store import list_providers

    return {"providers": list_providers(mask_secret=True)}


def active_provider_payload() -> dict:
    from provider_store import get_active_provider

    return {"active": _provider_payload(get_active_provider())}


def get_provider_payload(provider_id: str) -> tuple[dict, int]:
    from provider_store import get_provider

    provider = _provider_payload(get_provider(provider_id))
    return ({"provider": provider}, 200) if provider is not None else ({"error": "Provider not found"}, 404)


def save_provider_payload(body: dict, provider_id: str | None = None) -> tuple[dict, int]:
    from provider_store import mask_api_key, upsert_provider

    data = dict(body)
    if provider_id is not None:
        data["id"] = provider_id
    result = upsert_provider(data)
    result["api_key_masked"] = mask_api_key(result.pop("api_key", ""))
    return {"ok": True, "provider": result}, 200 if provider_id is not None else 201


def delete_provider_payload(provider_id: str) -> dict:
    from provider_store import delete_provider

    delete_provider(provider_id)
    return {"ok": True}


def activate_provider_payload(provider_id: str) -> dict:
    from provider_store import set_active_provider

    set_active_provider(provider_id)
    return {"ok": True, "active": provider_id}


def test_provider_payload(provider_id: str) -> dict:
    from provider_store import get_provider, mask_api_key, test_provider_connection

    payload = dict(test_provider_connection(provider_id) or {})
    provider = get_provider(provider_id) or {}
    api_key = str(provider.get("api_key") or "")
    error = str(payload.get("error") or "")
    if api_key:
        error = error.replace(api_key, "[redacted-api-key]")
        masked = mask_api_key(api_key)
        if masked:
            error = error.replace(masked, "[redacted-api-key]")
    if "error" in payload:
        payload["error"] = error[:300]
    return payload


def list_profile_payload() -> dict:
    from language_profile_store import list_language_profiles

    return {"profiles": list_language_profiles()}


def active_profile_payload() -> dict:
    from language_profile_store import get_active_language_profile

    return {"active": get_active_language_profile()}


def get_profile_payload(profile_id: str) -> tuple[dict, int]:
    from language_profile_store import get_language_profile

    profile = get_language_profile(profile_id)
    return ({"profile": profile}, 200) if profile is not None else ({"error": "Language Profile not found"}, 404)


def save_profile_payload(body: dict, profile_id: str | None = None) -> tuple[dict, int]:
    from language_profile_store import upsert_language_profile, validate_language_profile

    data = dict(body)
    if profile_id is not None:
        data["id"] = profile_id
    errors = validate_language_profile(data)
    if errors:
        raise ValueError("; ".join(errors))
    result = upsert_language_profile(data)
    return {"ok": True, "profile": result}, 200 if profile_id is not None else 201


def delete_profile_payload(profile_id: str) -> dict:
    from language_profile_store import delete_language_profile

    delete_language_profile(profile_id)
    return {"ok": True}


def activate_profile_payload(profile_id: str) -> dict:
    from language_profile_store import set_active_language_profile

    set_active_language_profile(profile_id)
    return {"ok": True, "active": profile_id}


def provider_templates_payload() -> dict:
    from provider_store import get_provider_templates

    return {"ok": True, "templates": get_provider_templates()}


def first_query_value(query: dict, key: str) -> str:
    values = query.get(key) or [""]
    return str(values[0] or "").strip()


def effective_translation_config(query: dict | None = None) -> dict:
    """Resolve Provider/Profile settings without writing local configuration."""
    query = query or {}
    provider_id = first_query_value(query, "provider_id") or first_query_value(query, "provider")
    profile_id = first_query_value(query, "language_profile_id") or first_query_value(
        query, "language_profile"
    )
    warnings: list[str] = []
    provider_summary = {
        "id": "", "name": "", "protocol": "", "model": "", "api_key_present": False,
        "api_key_masked": "", "status": "not_configured",
    }
    try:
        from provider_store import get_active_provider, get_provider, mask_api_key

        provider = get_provider(provider_id) if provider_id else get_active_provider()
        if provider:
            api_key = str(provider.get("api_key") or "")
            provider_summary = {
                "id": str(provider.get("id") or ""),
                "name": str(provider.get("name") or provider.get("id") or ""),
                "protocol": str(provider.get("protocol") or "openai-compatible"),
                "model": str(provider.get("translation_model") or ""),
                "api_key_present": bool(api_key),
                "api_key_masked": mask_api_key(api_key) if api_key else "",
                "status": "ok" if provider.get("enabled", True) else "disabled",
            }
            if not provider_summary["model"]:
                warnings.append("Selected Provider has no translation model.")
            if not api_key:
                warnings.append("Selected Provider has no API key.")
        else:
            warnings.append("No Provider is configured for translation.")
    except Exception as exc:
        provider_summary["status"] = "error"
        warnings.append(f"Provider preview failed: {exc}")

    profile_summary = {
        "id": "", "name": "", "type": "default", "source_language": "auto",
        "target_language": "zh-CN", "style_present": False, "glossary_count": 0,
        "quality": {}, "quality_source": "default", "status": "not_configured",
        "translation_reliability": {"mode": "off", "max_extra_requests": 12},
    }
    try:
        from language_profile_store import (
            get_active_language_profile,
            get_language_profile,
            list_language_profiles,
            normalize_glossary,
        )

        profile = get_language_profile(profile_id) if profile_id else get_active_language_profile()
        listed = {item.get("id"): item for item in list_language_profiles()}
        if profile:
            listed_profile = listed.get(profile.get("id"), profile)
            glossary = normalize_glossary(profile.get("glossary", []))
            is_builtin = bool(listed_profile.get("builtin", False))
            profile_summary = {
                "id": str(profile.get("id") or ""),
                "name": str(profile.get("name") or profile.get("id") or ""),
                "type": "builtin" if is_builtin else "local",
                "source_language": str(profile.get("source_language") or "auto"),
                "target_language": str(profile.get("target_language") or "zh-CN"),
                "style_present": bool(str(profile.get("translation_style") or "").strip()),
                "glossary_count": len(glossary),
                "quality": profile.get("quality", {}) if isinstance(profile.get("quality"), dict) else {},
                "quality_source": "builtin" if is_builtin else "local",
                "translation_reliability": profile.get(
                    "translation_reliability",
                    {"mode": "off", "max_extra_requests": 12},
                ),
                "status": "ok",
            }
        else:
            warnings.append("No Language Profile is configured.")
    except Exception as exc:
        profile_summary["status"] = "error"
        warnings.append(f"Language Profile preview failed: {exc}")
    return {
        "ok": True,
        "provider": provider_summary,
        "language_profile": profile_summary,
        "prompt_behavior": {
            "custom_prompt_overrides_profile_style": True,
            "glossary_always_appended": True,
        },
        "cache_behavior": {
            "key_includes_effective_prompt": True,
            "note": "Translation cache entries vary by effective prompt; style or glossary changes create separate cache entries.",
        },
        "warnings": warnings,
    }
