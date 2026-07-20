from __future__ import annotations

from types import SimpleNamespace

import pytest

from translation_strategy import (
    build_scene_batches,
    normalize_translation_strategy,
    validate_reflection,
)


def _item(index: int, start: int, end: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        time_line=f"00:00:{start:02d},000 --> 00:00:{end:02d},000",
        text=text,
    )


def test_scene_batches_split_at_scene_gap_and_hard_batch_limit() -> None:
    items = [
        _item(1, 0, 1, "a"),
        _item(2, 2, 3, "b"),
        _item(3, 4, 5, "c"),
        _item(4, 20, 21, "d"),
    ]
    batches = build_scene_batches(
        items, batch_size=2, context_window=1, scene_gap_seconds=10
    )
    assert [[row["id"] for row in batch["items"]] for batch in batches] == [
        [1], [2, 3], [4],
    ]
    assert batches[1]["context_before"] == [{"id": 1, "text": "a"}]


def test_translation_strategy_and_reflection_are_strict() -> None:
    assert normalize_translation_strategy("three-pass")["mode"] == "three_pass"
    issues, summary = validate_reflection(
        {
            "issues": [
                {"id": 1, "issues": []},
                {"id": 2, "issues": ["tone"]},
            ],
            "scene_summary": "Two people greet each other.",
        },
        [1, 2],
    )
    assert issues[2] == ["tone"]
    assert summary.startswith("Two people")
    with pytest.raises(RuntimeError, match="exactly match"):
        validate_reflection({"issues": [{"id": 1, "issues": []}]}, [1, 2])
