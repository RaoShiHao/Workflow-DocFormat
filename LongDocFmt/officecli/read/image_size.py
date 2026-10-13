"""Length parsing and size resolution for pictures (client-side aspect / page %)."""

from __future__ import annotations

import re
from typing import Any

_LENGTH_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*(cm|mm|in|pt|pc|px|Q|emu)?\s*$",
    re.IGNORECASE,
)

# 1 unit → cm
_TO_CM: dict[str, float] = {
    "cm": 1.0,
    "mm": 0.1,
    "in": 2.54,
    "pt": 2.54 / 72.0,
    "pc": 2.54 / 6.0,
    "px": 2.54 / 96.0,
    "q": 2.54 / (72.0 * 20.0),
    "emu": 2.54 / (914400.0 / 2.54),  # 1 inch = 914400 EMU
}


def parse_length_to_cm(value: Any) -> float | None:
    """Parse officecli length string (or bare number as cm) to centimetres."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = _LENGTH_RE.match(text)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "cm").lower()
    factor = _TO_CM.get(unit)
    if factor is None:
        return None
    return number * factor


def format_length_cm(value_cm: float, *, precision: int = 2) -> str:
    """Format cm value for officecli ``width`` / ``height`` props."""
    rounded = round(value_cm, precision)
    if rounded == int(rounded):
        return f"{int(rounded)}cm"
    return f"{rounded:.{precision}f}cm"


def content_width_cm_from_page_format(page_format: dict[str, Any]) -> float | None:
    """
    Text/content area width ≈ page width − left margin − right margin.

    Matches ref ``pt_to_percent`` logic (margin box, not full paper).
    """
    paper = page_format.get("paper") or {}
    margin = page_format.get("margin") or {}
    page_w = parse_length_to_cm(paper.get("width"))
    left = parse_length_to_cm(margin.get("left"))
    right = parse_length_to_cm(margin.get("right"))
    if page_w is None:
        return None
    left = left or 0.0
    right = right or 0.0
    width = page_w - left - right
    return width if width > 0 else None


def resolve_image_size_for_write(
    size: dict[str, Any],
    *,
    reference_width_cm: float | None = None,
    reference_height_cm: float | None = None,
    content_width_cm: float | None = None,
) -> tuple[dict[str, str], list[str]]:
    """
    Resolve ``size`` to officecli ``width`` / ``height`` strings.

    Rules:
    - ``width_percent``: absolute width = content area × percent / 100
      (officecli does not accept ``50%`` directly).
    - ``lock_aspect_ratio=true``:
      - one of width / height / width_percent → compute the missing dimension
      - both width and height → **width wins**, height recalculated
    - Aspect ratio from reference image dimensions (current size on document).
    """
    warnings: list[str] = []
    lock = size.get("lock_aspect_ratio") is True

    ref_w = reference_width_cm
    ref_h = reference_height_cm
    if ref_w is None:
        ref_w = parse_length_to_cm(size.get("width"))
    if ref_h is None:
        ref_h = parse_length_to_cm(size.get("height"))

    aspect: float | None = None
    if ref_w is not None and ref_h is not None and ref_h > 0:
        aspect = ref_w / ref_h

    width_val = size.get("width")
    height_val = size.get("height")
    width_percent = size.get("width_percent")

    if width_percent is not None:
        if content_width_cm is None or content_width_cm <= 0:
            warnings.append(
                "size.width_percent requires page content width; skipped percent."
            )
        else:
            width_val = format_length_cm(content_width_cm * float(width_percent) / 100.0)

    w_cm = parse_length_to_cm(width_val)
    h_cm = parse_length_to_cm(height_val)

    if lock:
        if aspect is None or aspect <= 0:
            warnings.append(
                "lock_aspect_ratio=true but no reference aspect ratio; "
                "pass current image size or both width and height once."
            )
        elif w_cm is not None:
            h_cm = w_cm / aspect
        elif h_cm is not None:
            w_cm = h_cm * aspect
        else:
            warnings.append(
                "lock_aspect_ratio=true requires width, height, or width_percent."
            )

    resolved: dict[str, str] = {}
    if w_cm is not None:
        resolved["width"] = format_length_cm(w_cm)
    if h_cm is not None:
        resolved["height"] = format_length_cm(h_cm)
    return resolved, warnings
