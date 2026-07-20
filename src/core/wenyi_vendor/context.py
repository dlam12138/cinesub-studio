"""Rolling translation context adapted from WenYi v0.3.2 (MIT)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RollingContext:
    recent_targets: list[dict] = field(default_factory=list)
    max_recent_keep: int = 40

    def render(self, count: int) -> list[dict]:
        if count <= 0:
            return []
        return [dict(row) for row in self.recent_targets[-count:]]

    def add(self, rows: list[dict]) -> None:
        self.recent_targets.extend(
            {"id": int(row["id"]), "translation": str(row["translation"])}
            for row in rows
            if str(row.get("translation") or "").strip()
        )
        if len(self.recent_targets) > self.max_recent_keep:
            self.recent_targets = self.recent_targets[-self.max_recent_keep:]
