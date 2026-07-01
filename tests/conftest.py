import re
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path(request):
    """Workspace-local temp directory that avoids cleanup in restricted sandboxes."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:80]
    path = Path("work") / "pytest-artifacts" / f"{safe_name}-{uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    return path
