"""CineSub Studio source package.

When this package is imported (e.g. via python -m), all subdirectories
are injected into sys.path so that cross-module imports like
`from provider_store import ...` continue to work without explicit
package prefixes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_src = Path(__file__).resolve().parent
for _sub in ["core", "pipeline", "config", "web", "tools"]:
    _subpath = _src / _sub
    if str(_subpath) not in sys.path:
        sys.path.insert(0, str(_subpath))
