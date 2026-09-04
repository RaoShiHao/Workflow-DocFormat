"""Image format schema (inline pictures only: size + host paragraph)."""

from __future__ import annotations

import re
from typing import Any

from .image_size import (
    parse_length_to_cm,
    resolve_image_size_for_write,
)


def parent_paragraph_path(picture_path: str) -> str | None:
    """Picture run path → parent paragraph (body ``/body/p[…]`` or table cell ``…/tc[…]/p[…]``)."""
    text = str(picture_path or "").strip()
    if not text:
        return None
    match = re.match(r"^(.*?/p\[[^\]]+\])", text)
    return match.group(1) if match else None


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "on", "yes"}:
        return True
    if text in {"false", "0", "off", "no"}:
        return False
    return bool(value)


def _scalar_from_fmt(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt:
            return fmt[key]
    return None


def is_inline_picture_fmt(fmt: dict[str, Any]) -> bool:
    """True when officecli reports an inline (non-floating) picture."""
    if _coerce_bool(fmt.get("anchor")) is True:
        return False
    wrap = _scalar_from_fmt(fmt, "wrap")
    if wrap is None:
        return True
    return str(wrap).strip().lower() == "inline"


_SIZE_WRITE_HELPER_KEYS = ("lock_aspect_ratio", "width_percent")


def build_image_size(fmt: dict[str, Any]) -> dict[str, Any]:
    """
    Build ``size`` from either:

    - raw officecli picture format (flat ``width`` / ``height``), or
    - already-normalized ``{\"size\": {\"width\", \"height\", ...}}``
      (as produced by :class:`~LongDocFormatter.officecli.read.image_reader.WordImageReader`
      and style ``format_config``).
    """
    nested = fmt.get("size") if isinstance(fmt.get("size"), dict) else None
    src = nested if nested is not None else fmt
    out: dict[str, Any] = {
        "width": _scalar_from_fmt(src, "width"),
        "height": _scalar_from_fmt(src, "height"),
    }
    for key in _SIZE_WRITE_HELPER_KEYS:
        if key in src:
            out[key] = src[key]
        elif nested is None and key in fmt:
            out[key] = fmt[key]
    return out


def build_image_format(fmt: dict[str, Any]) -> dict[str, Any]:
    """Inline picture writable groups: ``size`` only."""
    return {"size": build_image_size(fmt)}


def build_image_metadata(fmt: dict[str, Any]) -> dict[str, Any]:
    """Read-only identifiers (not migrated as format)."""
    file_size = fmt.get("fileSize")
    return {
        "picture_id": _scalar_from_fmt(fmt, "id"),
        "name": _scalar_from_fmt(fmt, "name"),
        "rel_id": _scalar_from_fmt(fmt, "relId", "rel_id"),
        "content_type": _scalar_from_fmt(fmt, "contentType", "content_type"),
        "file_size": int(file_size) if file_size is not None else None,
    }


def image_format_to_officecli_props(
    image_format: dict[str, Any],
    *,
    reference_size: dict[str, Any] | None = None,
    content_width_cm: float | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    Map ``image_format.size`` to officecli ``width`` / ``height``.

    Write-only helpers on ``size``: ``lock_aspect_ratio``, ``width_percent``.
    """
    props: dict[str, Any] = {}
    warnings: list[str] = []

    size = image_format.get("size") or {}
    ref = reference_size or {}
    resolved_size, size_warnings = resolve_image_size_for_write(
        size,
        reference_width_cm=parse_length_to_cm(ref.get("width") or size.get("width")),
        reference_height_cm=parse_length_to_cm(ref.get("height") or size.get("height")),
        content_width_cm=content_width_cm,
    )
    warnings.extend(size_warnings)
    if resolved_size.get("width") is not None:
        props["width"] = resolved_size["width"]
    if resolved_size.get("height") is not None:
        props["height"] = resolved_size["height"]

    return props, warnings
