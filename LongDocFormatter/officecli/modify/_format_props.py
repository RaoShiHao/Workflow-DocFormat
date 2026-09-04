"""Map ``base_font`` / ``advanced_font`` dicts to officecli ``set`` properties."""

from __future__ import annotations

from typing import Any

from LongDocFormatter.officecli.read.format_schema import normalize_highlight
from LongDocFormatter.officecli.read.text_reader import (
    RUN_ADVANCED_EFFECT_KEYS,
    RUN_BASE_EFFECT_KEYS,
)

# Reader run deltas use effect keys only; writer also accepts typography for forward compat.
RUN_BASE_FONT_KEYS = RUN_BASE_EFFECT_KEYS + (
    "name",
    "name_ascii",
    "name_far_east",
    "size",
)
RUN_ADVANCED_FONT_KEYS = RUN_ADVANCED_EFFECT_KEYS

# Only these keys are blasted to every run during tag migration (theme font binding).
RUN_TYPOGRAPHY_FONT_KEYS = frozenset(
    {
        "name",
        "name_ascii",
        "name_far_east",
        "size",
    }
)

_BASE_BOOL_OFFICE: dict[str, str] = {
    "bold": "bold",
    "italic": "italic",
}

_BOOL_ADV_MAP: dict[str, str] = {
    "strike": "strike",
    "double_strike": "dstrike",
    "caps": "caps",
    "small_caps": "smallcaps",
    "vanish": "vanish",
}

# Paragraph set has no run wrapper for these; apply on each child run (or --find fallback).
PARAGRAPH_RUN_SCOPE_OFFICE_KEYS = frozenset(
    {
        "caps",
        "smallcaps",
        "dstrike",
        "vanish",
        "superscript",
        "subscript",
        "charSpacing",
    }
)

# Backward-compatible alias used inside this module.
PARAGRAPH_FIND_ONLY_OFFICE_KEYS = PARAGRAPH_RUN_SCOPE_OFFICE_KEYS

# officecli run/paragraph font theme bindings (clear with ``""`` when setting explicit fonts).
FONT_THEME_OFFICE_KEYS = (
    "font.asciiTheme",
    "font.hAnsiTheme",
    "font.eaTheme",
    "font.csTheme",
)

EXPLICIT_FONT_OFFICE_KEYS = ("font", "font.latin", "font.ea")


def has_explicit_font_props(props: dict[str, Any]) -> bool:
    return any(
        props.get(key) is not None and props.get(key) != ""
        for key in EXPLICIT_FONT_OFFICE_KEYS
    )


def typography_base_font(base_font: dict[str, Any] | None) -> dict[str, Any]:
    """Subset of ``base_font`` safe to apply to every run (font names + size only)."""
    if not base_font:
        return {}
    return {
        key: base_font[key]
        for key in RUN_TYPOGRAPHY_FONT_KEYS
        if key in base_font
    }


def mark_rpr_typography_font(mark_rpr_font: dict[str, Any] | None) -> dict[str, Any]:
    """``mark_rpr_font`` subset migrated to paragraph ``pPr/rPr`` (no bold/italic)."""
    return typography_base_font(mark_rpr_font)


def migration_uniform_run_font_props(
    base_font: dict[str, Any] | None,
    *,
    source_base_font: dict[str, Any] | None = None,
    source_office_format: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Typography plus uniform ``bold`` / ``italic`` for tag migration onto every run."""
    props, _, warnings = font_format_to_officecli_props(
        typography_base_font(base_font),
        None,
        source_base_font=source_base_font,
        source_office_format=source_office_format,
        scope="run",
    )
    base = base_font or {}
    bool_subset = {k: base[k] for k in ("bold", "italic") if k in base}
    if bool_subset:
        bool_props, _, bool_warnings = font_format_to_officecli_props(
            bool_subset,
            None,
            source_base_font=source_base_font,
            source_office_format=source_office_format,
            scope="run",
            allow_typography=True,
        )
        props.update(bool_props)
        warnings.extend(bool_warnings)
    return props, warnings


def needs_uniform_run_font_migration(base_font: dict[str, Any] | None) -> bool:
    base = base_font or {}
    return bool(typography_base_font(base)) or any(k in base for k in ("bold", "italic"))


def augment_font_props_for_explicit_font(
    props: dict[str, Any],
    source_office_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    When writing explicit font names, clear theme bindings that would make Word
    display theme fonts (e.g. 等线 via ``majorHAnsi``) despite ``font.ea``.
    """
    if not has_explicit_font_props(props):
        return props
    out = dict(props)
    source = source_office_format or {}
    for key in FONT_THEME_OFFICE_KEYS:
        if source.get(key) is not None:
            out[key] = ""
    if out.get("size") is not None:
        source_size_cs = source.get("size.cs")
        if source_size_cs is not None and source_size_cs != out.get("size"):
            out["size.cs"] = out["size"]
    return out


def resolve_bool_write(*, target: bool, source: bool | None = None) -> bool | None:
    """
    Decide whether to emit a boolean officecli prop.

    - ``target=True`` → always write ``true``
    - ``target=False`` and source was on → write ``false`` (explicit clear)
    - ``target=False`` and source was off/absent → omit (avoid invalid/no-op XML)
    """
    source_on = bool(source) if source is not None else False
    if target is True:
        return True
    if target is False and source_on:
        return False
    return None


def assign_if_changed(
    props: dict[str, Any],
    key: str,
    target: Any,
    source: Any,
    *,
    clear_value: Any = None,
) -> None:
    """
    Write ``key`` only when ``target`` meaningfully differs from ``source``.

    Booleans use :func:`resolve_bool_write`. ``target is None`` with ``clear_value``
    emits a clear only when ``source`` is set and differs from the cleared state.
    """
    if isinstance(target, bool):
        resolved = resolve_bool_write(
            target=target,
            source=source if isinstance(source, bool) else None,
        )
        if resolved is not None:
            props[key] = resolved
        return

    if target is None:
        if clear_value is None:
            return
        if source is not None and source != clear_value:
            props[key] = clear_value
        return

    if target != source:
        props[key] = target


def _nested_get(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    cur: Any = data or {}
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur.get(key)
    return cur


def _write_bool_props(
    props: dict[str, Any],
    target_font: dict[str, Any],
    key_map: dict[str, str],
    *,
    source_font: dict[str, Any] | None = None,
) -> None:
    source_font = source_font or {}
    for src_key, office_key in key_map.items():
        if src_key not in target_font or not isinstance(target_font[src_key], bool):
            continue
        resolved = resolve_bool_write(
            target=target_font[src_key],
            source=source_font.get(src_key),
        )
        if resolved is not None:
            props[office_key] = resolved


def font_format_to_officecli_props(
    base_font: dict[str, Any] | None = None,
    advanced_font: dict[str, Any] | None = None,
    *,
    source_base_font: dict[str, Any] | None = None,
    source_advanced_font: dict[str, Any] | None = None,
    source_office_format: dict[str, Any] | None = None,
    scope: str = "run",
    allow_typography: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """
    Convert ``base_font`` / ``advanced_font`` to officecli props.

    Returns ``(paragraph_or_run_props, run_scope_props, warnings)``.

    When ``scope='paragraph'``, run-only keys (``caps``, ``charSpacing``, ``vanish``, …)
    go to ``run_scope_props`` and should be applied on child ``run`` nodes.

    Pass ``source_*`` from the document **before** write so ``false`` targets only
    emit clears when the source element was previously on.
    """
    props: dict[str, Any] = {}
    run_scope: dict[str, Any] = {}
    warnings: list[str] = []
    base = base_font or {}
    advanced = advanced_font or {}
    source_base = source_base_font or {}
    source_advanced = source_advanced_font or {}

    unknown_base = set(base) - set(RUN_BASE_FONT_KEYS)
    unknown_adv = set(advanced) - set(RUN_ADVANCED_FONT_KEYS)
    for key in sorted(unknown_base):
        warnings.append(f"Unsupported base_font key ignored: {key}")
    for key in sorted(unknown_adv):
        warnings.append(f"Unsupported advanced_font key ignored: {key}")

    def _route(office_key: str, value: Any) -> None:
        if scope == "paragraph" and office_key in PARAGRAPH_RUN_SCOPE_OFFICE_KEYS:
            run_scope[office_key] = value
        else:
            props[office_key] = value

    if allow_typography:
        if base.get("name") is not None:
            props["font"] = base["name"]
        if base.get("name_ascii") is not None:
            props["font.latin"] = base["name_ascii"]
        if base.get("name_far_east") is not None:
            props["font.ea"] = base["name_far_east"]
        if base.get("size") is not None:
            props["size"] = base["size"]

    base_bool_props: dict[str, Any] = {}
    if allow_typography:
        _write_bool_props(
            base_bool_props,
            base,
            _BASE_BOOL_OFFICE,
            source_font=source_base,
        )
    for office_key, value in base_bool_props.items():
        _route(office_key, value)

    if base.get("underline") is not None:
        _route("underline", base["underline"])
    if base.get("color") is not None:
        props["color"] = base["color"]
    if base.get("highlight") is not None:
        highlight = normalize_highlight(base["highlight"])
        if highlight is not None:
            props["highlight"] = highlight
        elif str(base["highlight"]).strip().lower() not in {"", "none"}:
            warnings.append(
                f"Unsupported highlight {base['highlight']!r}; skipped."
            )

    adv_bool_props: dict[str, Any] = {}
    _write_bool_props(
        adv_bool_props,
        advanced,
        _BOOL_ADV_MAP,
        source_font=source_advanced,
    )
    for office_key, value in adv_bool_props.items():
        _route(office_key, value)

    for src_key, office_key in (("superscript", "superscript"), ("subscript", "subscript")):
        if src_key not in advanced or not isinstance(advanced[src_key], bool):
            continue
        resolved = resolve_bool_write(
            target=advanced[src_key],
            source=source_advanced.get(src_key),
        )
        if resolved is not None:
            _route(office_key, resolved)

    if advanced.get("char_spacing") is not None:
        _route("charSpacing", advanced["char_spacing"])

    props = augment_font_props_for_explicit_font(props, source_office_format)
    if run_scope:
        run_scope = augment_font_props_for_explicit_font(
            run_scope,
            source_office_format,
        )

    return props, run_scope, warnings


def run_entry_to_officecli_props(
    run: dict[str, Any],
    *,
    source_run: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    Map one ``runs[]`` entry (``text`` + optional ``base_font`` / ``advanced_font``) to props.
    """
    warnings: list[str] = []
    if run.get("path"):
        warnings.append("runs[].path is read-only metadata; matching uses text only.")
    source = source_run or {}
    props, run_scope, more_warnings = font_format_to_officecli_props(
        run.get("base_font"),
        run.get("advanced_font"),
        source_base_font=source.get("base_font"),
        source_advanced_font=source.get("advanced_font"),
        source_office_format=(source_run or {}).get("office_format"),
        scope="run",
    )
    props.update(run_scope)
    warnings.extend(more_warnings)
    return props, warnings
