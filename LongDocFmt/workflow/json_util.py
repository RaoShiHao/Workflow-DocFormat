from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Sequence


class LlmJsonParseError(RuntimeError):
    """LLM returned text that is not usable JSON.

    Isolated to **that one call**. Callers skip this call's results and continue
    later batches, later layers, and later pipeline steps (apply still runs).
    """

    def __init__(self, message: str, *, layer: str = "", raw: str = ""):
        super().__init__(message)
        self.layer = layer
        self.raw = raw


_FENCE_RE = re.compile(r"```(?:json|jsonl|javascript)?\s*([\s\S]*?)```", re.I)

# Common top-level list keys produced by T/A/M (and similar) prompts.
DEFAULT_SALVAGE_LIST_KEYS: tuple[str, ...] = (
    "assignments",
    "tables",
    "items",
    "paragraphs",
    "patches",
    "declarations",
    "roles",
    "styles",
    "commands",
    "ops",
    "actions",
)

# Compact wire format: identity is the object key (style_id / location_id / cluster_id).
DEFAULT_SALVAGE_OBJECT_KEYS: tuple[str, ...] = (
    "loc",
    "styles",
    "declarations",
    "patches",
    "by_layer",
    "by_style_id",
    "section",
    "paragraph.body",
    "table",
    "image",
    "table_cells",
    "runs",
    "target_props",
    "add",
    "set",
)

_COMPACT_SEPARATORS = (",", ":")
_PASSTHROUGH_KEYS = (
    "tables",
    "assignments",
    "items",
    "paragraphs",
    "patches",
    "loc",
    "styles",
    "declarations",
    "by_layer",
    "by_style_id",
    "section",
    "paragraph.body",
    "table",
    "image",
    "table_cells",
    "runs",
    "target_props",
    "add",
    "set",
)


def compact_json_dumps(obj: Any) -> str:
    """LLM-facing JSON: no indent. Disk artifacts still use ``write_json`` (pretty)."""
    return json.dumps(obj, ensure_ascii=False, separators=_COMPACT_SEPARATORS)


def is_scalar_map(value: Any) -> bool:
    """True for ``{id: style_id}`` maps (values are scalars, not nested records)."""
    if not isinstance(value, dict) or not value:
        return False
    return all(not isinstance(v, (dict, list)) for v in value.values())


def keyed_records(payload: Any, *, id_key: str) -> list[dict[str, Any]]:
    """Normalize a list of objects or an id-keyed object into records with ``id_key``."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, val in payload.items():
        if isinstance(val, dict):
            row = dict(val)
            row.setdefault(id_key, key)
            rows.append(row)
        elif val is not None:
            rows.append({id_key: key, "value": val})
    return rows


def stable_json_dumps(obj: Any) -> str:
    def _default(x: Any) -> Any:
        try:
            return str(x)
        except Exception:
            return "<unserializable>"

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=_default)


def strip_llm_fences(text: str) -> str:
    chunks = _FENCE_RE.findall(text or "")
    if chunks:
        return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())
    return text or ""


def safe_json_loads(s: str) -> Dict[str, Any]:
    s = (s or "").strip()
    if not s:
        return {}
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else {"value": out}
    except Exception:
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            return {}
        try:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _slice_array_after_key(text: str, key: str) -> str | None:
    """Return substring starting at ``[`` for ``\"key\": [ ...`` (may be truncated)."""
    pattern = rf'"{re.escape(key)}"\s*:\s*\['
    match = re.search(pattern, text)
    if not match:
        return None
    return text[match.end() - 1 :]


def _slice_object_after_key(text: str, key: str) -> str | None:
    """Return substring starting at ``{`` for ``\"key\": { ...`` (may be truncated)."""
    pattern = rf'"{re.escape(key)}"\s*:\s*\{{'
    match = re.search(pattern, text)
    if not match:
        return None
    return text[match.end() - 1 :]


def salvage_json_object_pairs(object_text: str) -> dict[str, Any]:
    """Decode complete key/value pairs from a (possibly truncated) ``{ ...`` prefix."""
    raw = object_text or ""
    i = 0
    n = len(raw)
    while i < n and raw[i].isspace():
        i += 1
    if i >= n or raw[i] != "{":
        return {}
    i += 1
    decoder = json.JSONDecoder()
    out: dict[str, Any] = {}
    while i < n:
        while i < n and raw[i] in " \t\r\n,":
            i += 1
        if i >= n or raw[i] == "}":
            break
        try:
            key, end = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            break
        i = end
        while i < n and raw[i].isspace():
            i += 1
        if i >= n or raw[i] != ":":
            break
        i += 1
        while i < n and raw[i].isspace():
            i += 1
        try:
            value, end = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            break
        if isinstance(key, (str, int)):
            out[str(key)] = value
        i = end
    return out


def salvage_json_array_objects(array_text: str) -> list[Any]:
    """Decode complete JSON values from a (possibly truncated) ``[ ...`` prefix.

    Stops at the first incomplete value. Incomplete trailing objects are dropped;
    earlier complete objects are kept.
    """
    raw = array_text or ""
    i = 0
    n = len(raw)
    while i < n and raw[i].isspace():
        i += 1
    if i >= n or raw[i] != "[":
        return []
    i += 1
    decoder = json.JSONDecoder()
    items: list[Any] = []
    while i < n:
        while i < n and raw[i] in " \t\r\n,":
            i += 1
        if i >= n or raw[i] == "]":
            break
        try:
            value, end = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            break
        items.append(value)
        i = end
    return items


def salvage_partial_llm_json(
    text: str,
    *,
    list_keys: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Best-effort parse of truncated LLM JSON.

    Prefer a full ``json.loads``. On failure, recover complete objects under the
    first known list key (``assignments`` / ``tables`` / …). Returns ``{}`` if
    nothing usable is found.
    """
    blob = strip_llm_fences(text).strip()
    if not blob:
        return {}
    try:
        parsed = json.loads(blob)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    except json.JSONDecodeError:
        pass

    keys = tuple(list_keys) if list_keys is not None else DEFAULT_SALVAGE_LIST_KEYS
    for key in keys:
        array_text = _slice_array_after_key(blob, key)
        if array_text is None:
            continue
        items = salvage_json_array_objects(array_text)
        if items:
            return {key: items}
    for key in DEFAULT_SALVAGE_OBJECT_KEYS:
        object_text = _slice_object_after_key(blob, key)
        if object_text is None:
            continue
        pairs = salvage_json_object_pairs(object_text)
        if pairs:
            return {key: pairs}
    # Bare truncated array (no object wrapper).
    if blob.lstrip().startswith("["):
        items = salvage_json_array_objects(blob.lstrip())
        if items:
            return {"items": items}
    if blob.lstrip().startswith("{"):
        pairs = salvage_json_object_pairs(blob.lstrip())
        if pairs:
            return pairs
    return {}


def parse_llm_json(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict) and "content" in result:
        text = str(result.get("content") or "")
        parsed = safe_json_loads(text)
        if parsed:
            return parsed
        return salvage_partial_llm_json(text)
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        parsed = safe_json_loads(result)
        if parsed:
            return parsed
        return salvage_partial_llm_json(result)
    return {}


def llm_content_text(result: Any) -> str:
    if isinstance(result, dict) and "content" in result:
        return str(result.get("content") or "")
    if isinstance(result, str):
        return result
    return ""


def parse_llm_json_strict(
    result: Any,
    *,
    layer: str,
    list_keys: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Parse LLM JSON or raise.

    Truncated replies: salvage complete list items when possible instead of
    discarding the whole call.
    """
    if isinstance(result, dict) and result.get("success") is False:
        err = result.get("error") or "LLM call failed"
        raise LlmJsonParseError(f"{layer}: {err}", layer=layer, raw=llm_content_text(result)[:800])
    if isinstance(result, dict) and "content" not in result:
        if any(key in result for key in _PASSTHROUGH_KEYS):
            return result
    text = llm_content_text(result)
    blob = strip_llm_fences(text).strip()
    if blob:
        try:
            loaded = json.loads(blob)
            if isinstance(loaded, dict):
                return loaded
            if isinstance(loaded, list):
                return {"items": loaded}
        except json.JSONDecodeError:
            pass
    salvaged = salvage_partial_llm_json(text, list_keys=list_keys)
    if salvaged:
        return salvaged
    fallback = safe_json_loads(text)
    if fallback:
        return fallback
    n = len(text.strip())
    if n == 0:
        raise LlmJsonParseError(f"{layer}: empty LLM content", layer=layer, raw="")
    raise LlmJsonParseError(
        f"{layer}: invalid or truncated JSON ({n} chars)",
        layer=layer,
        raw=text[:800],
    )
