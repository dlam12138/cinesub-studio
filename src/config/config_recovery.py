from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class ConfigCorruptError(ValueError):
    def __init__(self, store: str, reason: str = "configuration cannot be read") -> None:
        self.store = store
        self.reason = reason
        super().__init__(f"{store} configuration is unavailable: {reason}")


class ConfigRecoveryError(RuntimeError):
    pass


def _spec(store: str) -> tuple[Path, dict, object]:
    if store == "providers":
        import provider_store as module
    elif store == "language_profiles":
        import language_profile_store as module
    else:
        raise ValueError("Unknown configuration store.")
    return module.CONFIG_PATH, dict(module.DEFAULT_EMPTY_CONFIG), module


def _validate_file(path: Path, store: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("root value must be an object")
    collection = "providers" if store == "providers" else "profiles"
    if collection in data and not isinstance(data[collection], list):
        raise ValueError(f"{collection} must be a list")
    if "active" in data and not isinstance(data["active"], str):
        raise ValueError("active must be a string")


def store_status(store: str) -> dict:
    path, _default, _module = _spec(store)
    if not path.exists():
        return {"store": store, "status": "not_configured", "recoverable": False}
    try:
        _validate_file(path, store)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {
            "store": store,
            "status": "config_error",
            "recoverable": True,
            "error": "The local configuration file cannot be read safely.",
        }
    return {"store": store, "status": "ok", "recoverable": False}


def all_config_status() -> dict:
    stores = [store_status("providers"), store_status("language_profiles")]
    return {"ok": True, "stores": stores, "has_config_error": any(s["status"] == "config_error" for s in stores)}


def recover_store(store: str, action: str) -> dict:
    if action != "backup_and_reset":
        raise ValueError("Only backup_and_reset is supported.")
    path, default, module = _spec(store)
    status = store_status(store)
    if status["status"] != "config_error":
        raise ConfigRecoveryError("Recovery is allowed only for a corrupt configuration.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.stem}.corrupt.{timestamp}.json")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigRecoveryError(
            "Could not read the corrupt configuration for backup; the original was not changed."
        ) from exc
    try:
        with backup.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigRecoveryError("Could not create the corruption backup; the original was not changed.") from exc

    payload = json.dumps(default, ensure_ascii=False, indent=2).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f"{store}_recover_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise ConfigRecoveryError("Backup succeeded but the safe reset could not be written.") from exc

    with module._cache_lock:
        module._cache = None
        module._cache_mtime = 0.0
    return {"ok": True, "store": store, "status": "ok", "backup_created": True}
