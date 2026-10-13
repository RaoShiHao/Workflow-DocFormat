"""Minimal JSON helpers vendored for LongDocFmtEval (no format_migration dependency)."""
from __future__ import annotations

import json
import re
from typing import Any


def _escape_unquoted_double_quotes_in_json_strings(text: str) -> str:
    """Best-effort repair when the model puts raw ``\"`` inside JSON string values."""
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escape:
            out.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
            else:
                j = i + 1
                while j < len(text) and text[j] in " \t\n\r":
                    j += 1
                if j < len(text) and text[j] in ",}]:":
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _try_load_json_dict(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def safe_json_loads(text: str) -> dict[str, Any]:
    content = (text or "").strip()
    if not content:
        return {}

    loaded = _try_load_json_dict(content)
    if loaded is not None:
        return loaded

    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        block = match.group(0)
        for candidate in (block, _escape_unquoted_double_quotes_in_json_strings(block)):
            loaded = _try_load_json_dict(candidate)
            if loaded is not None:
                return loaded

    return {}
