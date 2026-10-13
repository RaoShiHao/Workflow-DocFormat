"""Theme diversity helpers for Step 1.5.

Intended pipeline:
  1) LLM proposes N (theme, writing_requirements) pairs grounded in the Role library
  2) Optional embedding pairwise filter: keep pairs with sim < τ (default band 0.75–0.85)

Embedding filter is **not wired yet** (no embedding model configured). Callers should
invoke ``filter_themes_by_similarity`` which currently returns the input unchanged
and records a skipped status for the run log / meta.json.
"""
from __future__ import annotations

from typing import Any


# Suggested calibration band once an embedding backend exists.
DEFAULT_SIM_THRESHOLD = 0.80
SUGGESTED_TAU_LOW = 0.75
SUGGESTED_TAU_HIGH = 0.85


def theme_text_for_embedding(row: dict) -> str:
    """Text used for pairwise similarity (theme + writing)."""
    theme = str(row.get("theme") or "").strip()
    writing = str(row.get("writing") or row.get("writing_requirements") or "").strip()
    notes = str(row.get("notes") or row.get("supplementary_notes") or "").strip()
    parts = [theme, writing]
    if notes:
        parts.append(notes)
    return "\n".join(parts)


def filter_themes_by_similarity(
    rows: list[dict],
    *,
    threshold: float = DEFAULT_SIM_THRESHOLD,
    enabled: bool = False,
    model: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Filter theme rows so pairwise embedding similarity stays below ``threshold``.

    Currently a **stub**: when ``enabled`` is False (default) or no embedding backend
    is available, returns ``rows`` unchanged.

    Returns
    -------
    kept, meta
        kept: filtered (or original) rows
        meta: status for logging / themes_meta.json
    """
    meta: dict[str, Any] = {
        "similarity_filter": "skipped",
        "reason": "embedding backend not configured; diversity relies on LLM prompt only",
        "threshold": threshold,
        "suggested_tau_band": [SUGGESTED_TAU_LOW, SUGGESTED_TAU_HIGH],
        "model": model,
        "input_count": len(rows),
        "kept_count": len(rows),
        "dropped": [],
    }
    if not enabled:
        return list(rows), meta

    # Placeholder for future: load embedder, greedy keep if max sim to kept < threshold
    meta["similarity_filter"] = "skipped"
    meta["reason"] = (
        f"enabled=True but embedding model not implemented yet "
        f"(requested model={model!r}, τ={threshold})"
    )
    return list(rows), meta
