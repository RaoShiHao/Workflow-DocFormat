from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


class LlmJsonParseError(RuntimeError):
    """LLM returned text that is not usable JSON.

    Isolated to **that one call**. Callers skip this call's results and continue
    later batches, later layers, and later pipeline steps (apply still runs).
    """

    def __init__(self, message: str, *, layer: str = "", raw: str = ""):
        super().__init__(message)
        self.layer = layer
        self.raw = raw


def stable_json_dumps(obj: Any) -> str:
    def _default(x: Any) -> Any:
        try:
            return str(x)
        except Exception:
            return "<unserializable>"

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=_default)


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


def parse_llm_json(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict) and "content" in result:
        return safe_json_loads(str(result.get("content") or ""))
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        return safe_json_loads(result)
    return {}


def llm_content_text(result: Any) -> str:
    if isinstance(result, dict) and "content" in result:
        return str(result.get("content") or "")
    if isinstance(result, str):
        return result
    return ""


def parse_llm_json_strict(result: Any, *, layer: str) -> Dict[str, Any]:
    """Parse LLM JSON or raise. Empty / truncated / invalid replies fail **this call**."""
    if isinstance(result, dict) and result.get("success") is False:
        err = result.get("error") or "LLM call failed"
        raise LlmJsonParseError(f"{layer}: {err}", layer=layer, raw=llm_content_text(result)[:800])
    if isinstance(result, dict) and "content" not in result:
        if any(key in result for key in ("tables", "assignments", "items", "paragraphs", "patches")):
            return result
    text = llm_content_text(result)
    parsed = parse_llm_json(result)
    if parsed:
        return parsed
    n = len(text.strip())
    if n == 0:
        raise LlmJsonParseError(f"{layer}: empty LLM content", layer=layer, raw="")
    raise LlmJsonParseError(
        f"{layer}: invalid or truncated JSON ({n} chars)",
        layer=layer,
        raw=text[:800],
    )
