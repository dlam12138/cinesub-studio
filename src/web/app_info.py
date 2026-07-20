from __future__ import annotations

import os
from typing import Any

from runtime_paths import RuntimePaths, resolve_runtime_paths
from versioning import read_version

APP_VERSION = read_version()
VALID_BUILD_FLAVORS = {"unified", "development"}


def get_app_info(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return non-sensitive build metadata for the local UI."""

    resolved = paths or resolve_runtime_paths()
    packaged = resolved.layout == "packaged"
    default_flavor = "development" if not packaged else "unified"
    flavor = str(os.environ.get("CINESUB_BUILD_FLAVOR") or default_flavor).strip().lower()
    if flavor not in VALID_BUILD_FLAVORS:
        flavor = default_flavor

    return {
        "ok": True,
        "version": APP_VERSION,
        "build_flavor": flavor,
        "runtime_layout": resolved.layout,
        "packaged": packaged,
        "cuda_runtime_bundled": packaged and flavor == "unified",
    }
