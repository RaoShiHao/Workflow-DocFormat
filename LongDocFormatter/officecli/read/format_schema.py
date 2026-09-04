"""
Migration-oriented format field builders (flat groups, explicit defaults).

Convention for values in JSON:
- ``null`` — property not directly set on the OOXML node (inherit / default)
- ``false`` / ``0`` / ``"0pt"`` — explicitly set off or zero when officecli reports it
"""

from __future__ import annotations

from typing import Any, Callable

# Keys always emitted in migration groups (value may be null).
PAGINATION_CONTROL_KEYS = (
    "widow_control",
    "keep_with_next",
    "keep_together",
    "page_break_before",
    "word_wrap",
    "contextual_spacing",
)
SPACING_KEYS = ("line_spacing", "line_spacing_rule", "before", "after")
# officecli: 1x + lineRule auto ≈ Word 单倍行距
DEFAULT_SINGLE_LINE_SPACING = "1x"
INDENT_KEYS = (
    "left",
    "right",
    "first_line",
    "hanging",
    "first_line_chars",
    "hanging_chars",
)
LIST_KEYS = ("list_style", "num_id", "num_level", "num_fmt", "start")

# officecli ``highlight`` prop accepts named colors only (not ``#RRGGBB``).
OFFICECLI_HIGHLIGHT_VALUES: tuple[str, ...] = (
    "yellow",
    "green",
    "cyan",
    "magenta",
    "blue",
    "red",
    "darkBlue",
    "darkCyan",
    "darkGreen",
    "darkMagenta",
    "darkRed",
    "darkYellow",
    "darkGray",
    "lightGray",
    "black",
    "white",
    "none",
)

_HEX_TO_HIGHLIGHT: dict[str, str] = {
    "FFFFFF": "white",
    "000000": "black",
    "FFFF00": "yellow",
    "00FF00": "green",
    "00FFFF": "cyan",
    "FF00FF": "magenta",
    "0000FF": "blue",
    "FF0000": "red",
    "808080": "darkGray",
    "C0C0C0": "lightGray",
}


def normalize_highlight(value: Any) -> str | None:
    """Map reader shading/highlight values to officecli named ``highlight``.

    White (``#FFFFFF`` / ``white``) is treated as unset: OOXML often carries white
    ``w:shd`` that is invisible on a white page and is not Word UI highlight.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "false", "0", "white"}:
        return None
    hex_key = text.upper().lstrip("#")
    if hex_key == "FFFFFF":
        return None
    for named in OFFICECLI_HIGHLIGHT_VALUES:
        if named.lower() == "white":
            continue
        if text.lower() == named.lower():
            return named
    if len(hex_key) == 6 and all(c in "0123456789ABCDEF" for c in hex_key):
        mapped = _HEX_TO_HIGHLIGHT.get(hex_key)
        return None if mapped == "white" else mapped
    return None


def normalize_num_id(value: Any) -> Any:
    """Word ``numId=0`` means no list — treat as unset."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        num_id = int(float(text))
    except (TypeError, ValueError):
        return value
    return None if num_id <= 0 else num_id


BASE_FONT_BOOL_KEYS = ("bold", "italic")
ADVANCED_FONT_BOOL_KEYS = (
    "strike",
    "double_strike",
    "caps",
    "small_caps",
    "superscript",
    "subscript",
    "vanish",
)
TABLE_PAGINATION_KEYS = ("repeat_header", "allow_break_across_pages")


def _pick(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt and fmt[key] is not None:
            return fmt[key]
    return None


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


def _bool_from_fmt(fmt: dict[str, Any], name: str, *, default: bool = False) -> bool:
    direct = fmt.get(name)
    if direct is not None:
        coerced = _coerce_bool(direct)
        return coerced if coerced is not None else default
    effective = fmt.get(f"effective.{name}")
    if effective is not None:
        coerced = _coerce_bool(effective)
        return coerced if coerced is not None else default
    return default


def _scalar_from_fmt(fmt: dict[str, Any], *keys: str) -> Any:
    """Return value if explicitly present on node, else ``None`` (unset)."""
    for key in keys:
        if key in fmt:
            return fmt[key]
    return None


def build_bool_group(
    fmt: dict[str, Any],
    keys: tuple[str, ...],
    *,
    resolver: Callable[[dict[str, Any], str], bool] | None = None,
) -> dict[str, bool]:
    resolve = resolver or _bool_from_fmt
    return {key: bool(resolve(fmt, key)) for key in keys}


def build_scalar_group(
    fmt: dict[str, Any],
    mapping: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """``mapping``: output_key -> officecli keys to read (direct only)."""
    return {
        out_key: _scalar_from_fmt(fmt, *office_keys)
        for out_key, office_keys in mapping.items()
    }


def build_pagination_control(fmt: dict[str, Any]) -> dict[str, Any]:
    return build_bool_group(
        fmt,
        PAGINATION_CONTROL_KEYS,
        resolver=lambda f, k: _bool_from_fmt(f, k, default=False),
    )


def _spacing_has_explicit_line(fmt: dict[str, Any]) -> bool:
    return any(key in fmt for key in ("lineSpacing", "lineRule", "lineSpacingRule"))


def build_spacing(fmt: dict[str, Any]) -> dict[str, Any]:
    line_rule = _scalar_from_fmt(fmt, "lineRule", "lineSpacingRule")
    line_spacing = _scalar_from_fmt(fmt, "lineSpacing")
    rule_map = {"auto": "single", "atLeast": "exact", "exact": "exact"}
    mapped_rule = None
    if line_rule is not None:
        mapped_rule = rule_map.get(str(line_rule), line_rule)
    before = _scalar_from_fmt(fmt, "spaceBefore")
    after = _scalar_from_fmt(fmt, "spaceAfter")
    if not _spacing_has_explicit_line(fmt):
        # No ``w:spacing`` on paragraph → Word default 单倍行距 (not stored in OOXML).
        return {
            "line_spacing": DEFAULT_SINGLE_LINE_SPACING,
            "line_spacing_rule": "single",
            "before": before,
            "after": after,
        }
    return {
        "line_spacing": line_spacing,
        "line_spacing_rule": mapped_rule,
        "before": before,
        "after": after,
    }


def build_indent(fmt: dict[str, Any]) -> dict[str, Any]:
    return {
        "left": _scalar_from_fmt(fmt, "indent", "leftIndent"),
        "right": _scalar_from_fmt(fmt, "rightIndent"),
        "first_line": _scalar_from_fmt(fmt, "firstLineIndent"),
        "hanging": _scalar_from_fmt(fmt, "hangingIndent"),
        "first_line_chars": _scalar_from_fmt(fmt, "firstLineChars"),
        "hanging_chars": _scalar_from_fmt(fmt, "hangingChars"),
    }


def build_list_info(fmt: dict[str, Any]) -> dict[str, Any]:
    return {
        "list_style": _scalar_from_fmt(fmt, "listStyle"),
        "num_id": normalize_num_id(_scalar_from_fmt(fmt, "numId")),
        "num_level": _scalar_from_fmt(fmt, "numLevel", "ilvl"),
        "num_fmt": _scalar_from_fmt(fmt, "numFmt"),
        "start": _scalar_from_fmt(fmt, "start"),
    }


def build_alignment(fmt: dict[str, Any]) -> dict[str, Any]:
    raw = _pick(fmt, "align", "alignment", "effective.alignment")
    return {"alignment": str(raw).lower() if raw is not None else None}


def build_outline_level(fmt: dict[str, Any]) -> dict[str, Any]:
    return {"outline_level": _scalar_from_fmt(fmt, "outlineLvl", "outlineLevel")}
