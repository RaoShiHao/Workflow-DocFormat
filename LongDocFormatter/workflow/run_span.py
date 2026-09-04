"""Locate inline run fragments inside a paragraph (AutoDataBuild expected_runs)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from LongDocFormatter.workflow.contracts import DocElement

_PARA_CONTENT_LIMIT = 2000
_HINT_CONTEXT = 40


def locate_run_span(
    paragraph_text: str,
    *,
    text: str,
    text_before: str = "",
    text_after: str = "",
) -> Optional[tuple[int, int]]:
    """Return half-open [start, end) of ``text`` in the paragraph, or None.

    Placement matches AutoDataBuild ``_run_inline_spans`` (no splice/insert):
    1. ``text_before + text + text_after`` window;
    2. ``before+text`` or ``text+after``;
    3. ``text`` if it occurs exactly once.
    """
    host = paragraph_text or ""
    frag = text or ""
    if not host or not frag:
        return None
    before = text_before or ""
    after = text_after or ""

    def _at(start: int) -> Optional[tuple[int, int]]:
        end = start + len(frag)
        if 0 <= start < end <= len(host) and host[start:end] == frag:
            return (start, end)
        return None

    if before or after:
        window = f"{before}{frag}{after}"
        idx = host.find(window)
        if idx >= 0:
            found = _at(idx + len(before))
            if found:
                return found
        if before:
            i1 = host.find(f"{before}{frag}")
            if i1 >= 0:
                found = _at(i1 + len(before))
                if found:
                    return found
        if after:
            i2 = host.find(f"{frag}{after}")
            if i2 >= 0:
                found = _at(i2)
                if found:
                    return found

    first = host.find(frag)
    if first < 0:
        return None
    if host.find(frag, first + 1) < 0:
        return (first, first + len(frag))
    return None


def find_text_for_officecli(paragraph_text: str, start: int, end: int) -> Optional[str]:
    """Substring safe for ``officecli set --find`` (unique in the paragraph)."""
    if start < 0 or end <= start or end > len(paragraph_text or ""):
        return None
    frag = paragraph_text[start:end]
    if not frag:
        return None
    if paragraph_text.count(frag) != 1:
        return None
    return frag


def normalize_run_span(raw: Dict[str, Any], *, run_style: str) -> Optional[Dict[str, str]]:
    text = str(
        raw.get("text")
        or raw.get("runs_text")
        or raw.get("run_text")
        or raw.get("substr")
        or ""
    )
    if not str(run_style or "").strip() or not text:
        return None
    return {
        "text_before": str(raw.get("text_before") or ""),
        "text": text,
        "text_after": str(raw.get("text_after") or ""),
        "run_style": str(run_style).strip(),
    }


def inventory_span_cues(para: DocElement, runs: List[DocElement]) -> List[Dict[str, Any]]:
    """Optional residual hints from init delta runs; not the assignment target."""
    host = para.content or ""
    out: List[Dict[str, Any]] = []
    for run in runs:
        frag = str(run.content or "")
        before = ""
        after = ""
        rng = str((run.meta or {}).get("range") or "")
        if ":" in rng:
            try:
                a, b = rng.split(":", 1)
                start, end = int(a), int(b)
                if 0 <= start < end <= len(host):
                    frag = host[start:end] or frag
                    before = host[max(0, start - _HINT_CONTEXT) : start]
                    after = host[end : end + _HINT_CONTEXT]
            except ValueError:
                pass
        if not before and not after and frag and frag in host:
            i = host.find(frag)
            before = host[max(0, i - _HINT_CONTEXT) : i]
            after = host[i + len(frag) : i + len(frag) + _HINT_CONTEXT]
        if not frag:
            continue
        item: Dict[str, Any] = {
            "text_before": before,
            "text": frag[:80],
            "text_after": after,
        }
        if run.props:
            item["init_cues"] = dict(run.props)
        out.append(item)
    return out


def paragraph_payload_content(para: DocElement) -> str:
    return (para.content or "")[:_PARA_CONTENT_LIMIT]
