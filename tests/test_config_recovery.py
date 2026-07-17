from __future__ import annotations

import json
from pathlib import Path

import language_profile_store
import provider_store
from config_recovery import ConfigCorruptError, ConfigRecoveryError, recover_store, store_status


def test_corrupt_provider_requires_explicit_backup_and_reset(monkeypatch, tmp_path):
    path = tmp_path / "config" / "providers.local.json"
    path.parent.mkdir()
    original = b'{"providers": [broken-secret-bytes'
    path.write_bytes(original)
    monkeypatch.setattr(provider_store, "CONFIG_DIR", path.parent)
    monkeypatch.setattr(provider_store, "CONFIG_PATH", path)
    provider_store._cache = None

    assert store_status("providers")["status"] == "config_error"
    try:
        provider_store.list_providers()
    except ConfigCorruptError:
        pass
    else:
        raise AssertionError("corrupt provider configuration was treated as empty")

    result = recover_store("providers", "backup_and_reset")
    assert result["backup_created"] is True
    backups = list(path.parent.glob("providers.local.corrupt.*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert json.loads(path.read_text(encoding="utf-8")) == provider_store.DEFAULT_EMPTY_CONFIG

    try:
        recover_store("providers", "backup_and_reset")
    except ConfigRecoveryError:
        pass
    else:
        raise AssertionError("valid configuration must not be reset")


def test_builtin_profiles_remain_read_only_when_local_override_is_corrupt(monkeypatch, tmp_path):
    path = tmp_path / "language_profiles.local.json"
    path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(language_profile_store, "CONFIG_PATH", path)
    language_profile_store._clear_cache()
    profiles = language_profile_store.list_language_profiles()
    assert profiles
    assert all(profile.get("builtin") for profile in profiles)


def test_backup_failure_keeps_original_and_removes_partial_backup(monkeypatch, tmp_path):
    path = tmp_path / "providers.local.json"
    original = b"not-json-with-secret"
    path.write_bytes(original)
    monkeypatch.setattr(provider_store, "CONFIG_PATH", path)
    provider_store._cache = None
    real_open = Path.open

    class FailingBackup:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, data):
            self.path.write_bytes(data[:3])
            raise OSError("simulated backup failure")

        def flush(self):
            pass

        def fileno(self):
            return -1

    def fail_backup_open(self, mode="r", *args, **kwargs):
        if "x" in mode and ".corrupt." in self.name:
            failing = FailingBackup()
            failing.path = self
            return failing
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_backup_open)
    try:
        recover_store("providers", "backup_and_reset")
    except ConfigRecoveryError:
        pass
    else:
        raise AssertionError("backup failure must reject recovery")
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("providers.local.corrupt.*.json"))


def test_source_read_and_atomic_reset_failures_never_replace_original(monkeypatch, tmp_path):
    path = tmp_path / "providers.local.json"
    original = b"not-json"
    path.write_bytes(original)
    monkeypatch.setattr(provider_store, "CONFIG_PATH", path)
    provider_store._cache = None

    real_read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("denied")) if self == path else real_read_bytes(self))
    try:
        recover_store("providers", "backup_and_reset")
    except ConfigRecoveryError as exc:
        assert "original was not changed" in str(exc)
    else:
        raise AssertionError("unreadable source must reject recovery")
    assert real_read_bytes(path) == original

    monkeypatch.setattr(Path, "read_bytes", real_read_bytes)
    import config_recovery

    monkeypatch.setattr(config_recovery.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("denied")))
    try:
        recover_store("providers", "backup_and_reset")
    except ConfigRecoveryError as exc:
        assert "Backup succeeded" in str(exc)
    else:
        raise AssertionError("atomic reset failure must reject recovery")
    assert path.read_bytes() == original
    assert len(list(tmp_path.glob("providers.local.corrupt.*.json"))) == 1
