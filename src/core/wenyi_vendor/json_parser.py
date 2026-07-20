"""Tolerant model-JSON parser adapted from WenYi v0.3.2.

Copyright (c) 2025 BigDawnGhost, MIT License.
Upstream file: trans_novel/llm/json_parser.py at commit d07298e.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _repair_unescaped_quotes(text: str) -> str:
    result: list[str] = []
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            if char == '"':
                in_string = True
            result.append(char)
        elif char == "\\" and index + 1 < len(text):
            result.append(text[index:index + 2])
            index += 2
            continue
        elif char == '"':
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead] in " \t\r\n":
                lookahead += 1
            if lookahead >= len(text) or text[lookahead] in ",:]}":
                in_string = False
                result.append(char)
            else:
                result.append('\\"')
        else:
            result.append(char)
        index += 1
    return "".join(result)


def parse_json_loose(text: str) -> Any:
    """Parse a JSON value from common model wrappers without guessing fields."""
    value = str(text or "").strip()
    candidates = [value]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, re.I | re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    for candidate in tuple(candidates):
        for opening, closing in (("{", "}"), ("[", "]")):
            start, end = candidate.find(opening), candidate.rfind(closing)
            if start >= 0 and end > start:
                candidates.append(candidate[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            pass
    starts = [position for position in (value.find("{"), value.find("[")) if position >= 0]
    for candidate in (value, _repair_unescaped_quotes(value)):
        try:
            if starts:
                parsed, _ = json.JSONDecoder().raw_decode(candidate[min(starts):])
                return parsed
        except (TypeError, json.JSONDecodeError):
            pass
    for candidate in candidates:
        try:
            return json.loads(_repair_unescaped_quotes(candidate))
        except (TypeError, json.JSONDecodeError):
            pass
    raise ValueError("model output did not contain a parseable JSON value")
