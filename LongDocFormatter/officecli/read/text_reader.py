"""Read Word paragraph/run text format via officecli (flat schema, aligned with ref)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ._cli import get_element, query_elements
from .theme_font_resolver import DocumentFontContext
from .format_schema import (
    build_alignment,
    build_indent,
    build_list_info,
    build_outline_level,
    build_pagination_control,
    build_spacing,
    normalize_highlight,
)

def _path_index(path: str, element: str) -> int | None:
    match = re.search(rf"/{element}\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _omit_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _pick(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt and fmt[key] is not None:
            return fmt[key]
    return None


def _coerce_bool(value: Any) -> bool | None:
    """Normalize officecli bools; return None when unknown."""
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


def _bool_from_fmt(fmt: dict[str, Any], name: str) -> bool | None:
    """
    Read a toggle from direct property or ``effective.<name>``.

    officecli 仅在 OOXML 里「有写入」时才出现 direct 键；
    未显式设置时键不存在（不是 false）。``effective.*`` 表示样式继承后的实际效果。
    """
    direct = fmt.get(name)
    if direct is not None:
        return _coerce_bool(direct)
    effective = fmt.get(f"effective.{name}")
    if effective is not None:
        return _coerce_bool(effective)
    return None


def _run_text_length(run: dict[str, Any]) -> int:
    return len(run.get("text") or "")


def _collect_runs(run_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in run_nodes if c.get("type") == "run"]


def _dominant_bool_by_weight(
    runs: list[dict[str, Any]],
    key: str,
    group_builder: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    para_fallback: dict[str, Any] | None = None,
) -> bool:
    """
    Pick True/False by character count across runs.

    Run without explicit ``key`` counts as False (not enabled).
    """
    true_weight = 0
    false_weight = 0
    for run in runs:
        weight = _run_text_length(run)
        if weight <= 0:
            continue
        run_fmt = dict(run.get("format") or {})
        value = group_builder(run_fmt).get(key)
        if value is True:
            true_weight += weight
        else:
            false_weight += weight

    if true_weight == 0 and false_weight == 0:
        fallback = (para_fallback or {}).get(key) if para_fallback else None
        return fallback if isinstance(fallback, bool) else False
    return true_weight > false_weight


_UNSET = object()


def _dominant_value_by_weight(
    runs: list[dict[str, Any]],
    getter: Callable[[dict[str, Any]], Any],
) -> Any:
    """Majority value by character length (typography: size, font name, …)."""
    weights: dict[Any, int] = defaultdict(int)
    for run in runs:
        weight = _run_text_length(run)
        if weight <= 0:
            continue
        run_fmt = dict(run.get("format") or {})
        value = getter(run_fmt)
        if value is not None:
            weights[value] += weight
    if not weights:
        return None
    return max(weights.keys(), key=lambda k: weights[k])


def _dominant_optional_by_weight(
    runs: list[dict[str, Any]],
    getter: Callable[[dict[str, Any]], Any],
) -> Any:
    """
    Optional effects (color, underline, …): treat «unset» as a competing state.

    Only returns a value when it wins by character count over «unset».
    """
    weights: dict[Any, int] = defaultdict(int)
    for run in runs:
        weight = _run_text_length(run)
        if weight <= 0:
            continue
        run_fmt = dict(run.get("format") or {})
        value = getter(run_fmt)
        bucket = _UNSET if value is None else value
        weights[bucket] += weight
    if not weights:
        return None
    winner = max(weights.keys(), key=lambda k: weights[k])
    if winner is _UNSET:
        return None
    return winner


def _normalize_underline(value: Any) -> Any:
    if value is not None and str(value).lower() in {"none", "false", "0"}:
        return None
    return value


def _underline_from_fmt(fmt: dict[str, Any]) -> Any:
    return _normalize_underline(_pick(fmt, "underline", "effective.underline"))


def _dominant_underline_from_runs(
    runs: list[dict[str, Any]],
    para_fmt: dict[str, Any],
) -> Any:
    """
    Paragraph underline from run character majority; fall back to paragraph markup
    when runs carry no underline evidence.
    """
    dominant = _dominant_optional_by_weight(runs, _underline_from_fmt)
    if dominant is not None:
        return dominant
    return _underline_from_fmt(para_fmt)


def _apply_explicit_bools(
    font: dict[str, Any],
    bool_keys: tuple[str, ...],
    dominance: dict[str, bool],
) -> dict[str, Any]:
    """Output readable bool keys as explicit True/False (for migration)."""
    result = dict(font)
    for key in bool_keys:
        result[key] = bool(dominance.get(key, False))
    return result


def _logical_font_for_diff(
    output_font: dict[str, Any],
    bool_keys: tuple[str, ...],
    dominance: dict[str, bool],
) -> dict[str, Any]:
    """Full logical state for run diff (includes False booleans)."""
    logical = dict(output_font)
    for key in bool_keys:
        logical[key] = dominance.get(key, False)
    return logical


def _build_main_font_from_runs(
    para_fmt: dict[str, Any],
    run_nodes: list[dict[str, Any]],
    *,
    font_context: DocumentFontContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bool], dict[str, bool]]:
    """
    Paragraph main font: typography from paragraph markup; bool effects
    (bold/italic/…) and underline from **run character-weight majority**.

    Short bold labels + long plain body → main ``bold=False``, bold stays in
    ``runs[]`` deltas — not the reverse (which would bold the whole paragraph).
    """
    para_base = _build_base_font(para_fmt)
    para_adv = _build_advanced_font(para_fmt)
    base_bool_keys = ("bold", "italic")
    adv_bool_keys = (
        "strike",
        "double_strike",
        "caps",
        "small_caps",
        "superscript",
        "subscript",
        "vanish",
    )

    runs = _collect_runs(run_nodes)
    base_dom = {
        k: _dominant_bool_by_weight(
            runs, k, _build_base_font, para_fallback=para_base
        )
        for k in base_bool_keys
    }
    adv_dom = {
        k: _dominant_bool_by_weight(
            runs, k, _build_advanced_font, para_fallback=para_adv
        )
        for k in adv_bool_keys
    }

    main_base = _apply_explicit_bools(para_base, base_bool_keys, base_dom)
    main_base.pop("underline", None)
    main_base = _enrich_base_font_from_para_markup(main_base, para_fmt)
    main_base = _enrich_base_font_from_theme(
        main_base,
        para_fmt,
        run_nodes,
        font_context,
    )
    dominant_underline = _dominant_underline_from_runs(runs, para_fmt)
    if dominant_underline is not None:
        main_base["underline"] = dominant_underline

    main_adv = _apply_explicit_bools(para_adv, adv_bool_keys, adv_dom)
    return main_base, main_adv, base_dom, adv_dom


def _style_name(fmt: dict[str, Any]) -> str | None:
    raw = _pick(fmt, "styleName", "style_name")
    if raw is not None:
        return str(raw)
    style_id = _pick(fmt, "styleId", "style")
    if style_id is None:
        return None
    text = str(style_id)
    if re.match(r"^a\d+$", text, re.IGNORECASE):
        return None
    return text


def _in_table(path: str) -> bool:
    return "/tbl[" in path or "/tc[" in path


def _build_base_font(fmt: dict[str, Any]) -> dict[str, Any]:
    """Maps ref ``base_font`` — only keys explicitly reported by officecli."""
    underline = _underline_from_fmt(fmt)
    return _omit_none(
        {
            "name": _pick(fmt, "font", "font.latin", "effective.font.ascii"),
            "name_ascii": _pick(
                fmt, "font.latin", "font.ascii", "effective.font.ascii"
            ),
            "name_far_east": _pick(
                fmt, "font.ea", "font.eastAsia", "effective.font.eastAsia"
            ),
            "size": _pick(fmt, "size", "effective.size"),
            "bold": _bool_from_fmt(fmt, "bold") or False,
            "italic": _bool_from_fmt(fmt, "italic") or False,
            "underline": underline,
            "color": _pick(fmt, "color", "effective.color"),
            "highlight": normalize_highlight(
                _pick(fmt, "highlight", "shading.fill", "shd")
            ),
        }
    )


def _build_mark_rpr_font(para_fmt: dict[str, Any]) -> dict[str, Any]:
    """Paragraph ``pPr/rPr`` (markRPr) typography only (font names + size).

    ``markRPr`` bold/italic/underline often disagree with visible run text (Word
    list-marker defaults); those effects come from ``base_font`` / ``runs[]``.
    """
    return _omit_none(
        {
            "name": _pick(
                para_fmt,
                "markRPr.font",
                "markRPr.font.latin",
            ),
            "name_ascii": _pick(para_fmt, "markRPr.font.latin"),
            "name_far_east": _pick(
                para_fmt,
                "markRPr.font.ea",
                "markRPr.font.eastAsia",
            ),
            "size": para_fmt.get("markRPr.size"),
        }
    )


PARAGRAPH_LEVEL_BASE_EFFECT_KEYS = ("underline", "color", "highlight")


def _paragraph_level_base_effects(base_font: dict[str, Any] | None) -> dict[str, Any]:
    """Effects on ``pPr/rPr`` that are not run typography (size/font names)."""
    if not base_font:
        return {}
    return {
        key: base_font[key]
        for key in PARAGRAPH_LEVEL_BASE_EFFECT_KEYS
        if key in base_font
    }


def _font_triplet_from_para_markup(para_fmt: dict[str, Any]) -> dict[str, str]:
    """Fonts on paragraph markRPr / effective when runs omit explicit rFonts."""
    return _omit_none(
        {
            "name": _pick(
                para_fmt,
                "font",
                "font.latin",
                "effective.font.ascii",
                "markRPr.font",
                "markRPr.font.latin",
            ),
            "name_ascii": _pick(
                para_fmt,
                "font.latin",
                "font.ascii",
                "effective.font.ascii",
                "markRPr.font.latin",
            ),
            "name_far_east": _pick(
                para_fmt,
                "font.ea",
                "font.eastAsia",
                "effective.font.eastAsia",
                "markRPr.font.ea",
                "markRPr.font.eastAsia",
            ),
        }
    )


def _enrich_base_font_from_para_markup(
    base_font: dict[str, Any],
    para_fmt: dict[str, Any],
) -> dict[str, Any]:
    markup = _font_triplet_from_para_markup(para_fmt)
    out = dict(base_font or {})
    for key in ("name", "name_ascii", "name_far_east"):
        if not out.get(key) and markup.get(key):
            out[key] = markup[key]
    return out


_THEME_REF_KEYS_EA = (
    "font.eaTheme",
    "font.eastAsiaTheme",
    "markRPr.font.eaTheme",
    "markRPr.font.eastAsiaTheme",
)
_THEME_REF_KEYS_LATIN = (
    "font.hAnsiTheme",
    "font.asciiTheme",
    "markRPr.font.hAnsiTheme",
    "markRPr.font.asciiTheme",
)
_FONT_HINT_KEYS = ("font.hint", "markRPr.font.hint")


def _pick_theme_ref(fmt: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = fmt.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _dominant_theme_ref_by_weight(
    runs: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> str | None:
    return _dominant_value_by_weight(
        runs,
        lambda rf: _pick_theme_ref(rf, *keys),
    )


def _dominant_font_hint_by_weight(runs: list[dict[str, Any]]) -> str | None:
    return _dominant_value_by_weight(
        runs,
        lambda rf: _pick_theme_ref(rf, *_FONT_HINT_KEYS),
    )


def _enrich_base_font_from_theme(
    base_font: dict[str, Any],
    para_fmt: dict[str, Any],
    run_nodes: list[dict[str, Any]],
    font_context: DocumentFontContext | None,
) -> dict[str, Any]:
    """
    Resolve fonts from doc theme when runs only carry ``font.hint`` / theme refs.

    Word UI may show e.g. 宋体 via ``docDefaults.eastAsiaTheme`` → theme Hans script
    even when run OOXML has no explicit ``font.ea``.
    """
    if font_context is None:
        return base_font or {}

    out = dict(base_font or {})
    needs_ea = not out.get("name_far_east")
    needs_latin = not out.get("name") and not out.get("name_ascii")
    if not needs_ea and not needs_latin:
        return out

    runs = _collect_runs(run_nodes)
    dominant_hint = _dominant_font_hint_by_weight(runs)
    para_hint = _pick_theme_ref(para_fmt, *_FONT_HINT_KEYS)

    resolved_ea: str | None = None
    if needs_ea:
        ea_theme = _dominant_theme_ref_by_weight(runs, _THEME_REF_KEYS_EA)
        ea_theme = ea_theme or _pick_theme_ref(para_fmt, *_THEME_REF_KEYS_EA)
        use_ea_chain = (
            ea_theme is not None
            or dominant_hint == "eastAsia"
            or para_hint == "eastAsia"
        )
        if use_ea_chain:
            ref = ea_theme or font_context.defaults.get("eastAsiaTheme")
            resolved_ea = font_context.resolve_theme_ref(ref)

    resolved_latin: str | None = None
    if needs_latin:
        latin_theme = _dominant_theme_ref_by_weight(runs, _THEME_REF_KEYS_LATIN)
        latin_theme = latin_theme or _pick_theme_ref(para_fmt, *_THEME_REF_KEYS_LATIN)
        ref = (
            latin_theme
            or font_context.defaults.get("hAnsiTheme")
            or font_context.defaults.get("asciiTheme")
        )
        resolved_latin = font_context.resolve_theme_ref(ref)

    if resolved_ea and not out.get("name_far_east"):
        out["name_far_east"] = resolved_ea
    if resolved_latin and not out.get("name_ascii"):
        out["name_ascii"] = resolved_latin
    if not out.get("name"):
        if dominant_hint == "eastAsia" or para_hint == "eastAsia":
            out["name"] = out.get("name_far_east") or resolved_ea or resolved_latin
        else:
            out["name"] = resolved_latin or out.get("name_far_east")
    return out


def _build_advanced_font(fmt: dict[str, Any]) -> dict[str, Any]:
    """Maps ref ``advanced_font`` — only keys explicitly reported."""
    superscript = _bool_from_fmt(fmt, "superscript")
    if superscript is None:
        vert = _pick(fmt, "vertAlign")
        if vert and str(vert).lower() in {"superscript", "sup"}:
            superscript = True
    subscript = _bool_from_fmt(fmt, "subscript")
    if subscript is None:
        vert = _pick(fmt, "vertAlign")
        if vert and str(vert).lower() in {"subscript", "sub"}:
            subscript = True
    return _omit_none(
        {
            "strike": _bool_from_fmt(fmt, "strike") or False,
            "double_strike": _bool_from_fmt(fmt, "dstrike") or False,
            "caps": _bool_from_fmt(fmt, "caps") or False,
            "small_caps": _bool_from_fmt(fmt, "smallcaps") or False,
            "superscript": superscript if superscript is not None else False,
            "subscript": subscript if subscript is not None else False,
            "char_spacing": _pick(fmt, "charspacing", "charSpacing"),
            "vanish": _bool_from_fmt(fmt, "vanish") or False,
        }
    )


# Run 片段只保留「效果类」属性相对段落主格式的差异（不含字体名、字号、下划线等段落级信息）
RUN_BASE_EFFECT_KEYS = (
    "bold",
    "italic",
    "color",
    "highlight",
)
RUN_ADVANCED_EFFECT_KEYS = (
    "strike",
    "double_strike",
    "caps",
    "small_caps",
    "superscript",
    "subscript",
    "char_spacing",
    "vanish",
)


def _resolve_run_effect(
    run_font: dict[str, Any],
    key: str,
    main_font: dict[str, Any],
) -> Any:
    """Resolve run effect; missing bool on run means False when main has bool."""
    if key in run_font:
        return run_font[key]
    if isinstance(main_font.get(key), bool):
        return False
    return None


_RUN_BOOL_EFFECT_KEYS = frozenset(
    {
        "bold",
        "italic",
        "strike",
        "double_strike",
        "caps",
        "small_caps",
        "superscript",
        "subscript",
        "vanish",
    }
)


def _diff_effect_font(
    run_font: dict[str, Any],
    main_font: dict[str, Any],
    effect_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Keep effect keys whose resolved value differs from paragraph main (output omits False)."""
    delta: dict[str, Any] = {}
    for key in effect_keys:
        run_value = _resolve_run_effect(run_font, key, main_font)
        main_value = main_font.get(key)
        if run_value is None and main_value is None:
            continue
        if run_value != main_value:
            if run_value is None:
                continue
            delta[key] = run_value
    return _omit_none(delta)


def _build_distinct_runs(
    run_nodes: list[dict[str, Any]],
    main_base_font: dict[str, Any],
    main_advanced_font: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    When a paragraph has 2+ runs, keep runs whose **effects** (bold, italic, …)
    differ from the paragraph main format. Each run only lists changed effect keys.
    """
    runs = [c for c in run_nodes if c.get("type") == "run"]
    if len(runs) < 2:
        return []

    distinct: list[dict[str, Any]] = []
    for run in runs:
        run_fmt = dict(run.get("format") or {})
        run_base = _build_base_font(run_fmt)
        run_adv = _build_advanced_font(run_fmt)
        base_delta = _diff_effect_font(run_base, main_base_font, RUN_BASE_EFFECT_KEYS)
        adv_delta = _diff_effect_font(run_adv, main_advanced_font, RUN_ADVANCED_EFFECT_KEYS)
        if not base_delta and not adv_delta:
            continue
        text = (run.get("text") or "").strip()
        entry = _omit_none(
            {
                "path": run.get("path"),
                "text": text or None,
                "base_font": base_delta or None,
                "advanced_font": adv_delta or None,
            }
        )
        distinct.append(entry)
    return distinct


# alignment / spacing / indent / list / pagination — see format_schema (migration keys, null=unset)


def _resolve_style_name(
    para_fmt: dict[str, Any],
    node_style: str | None = None,
) -> str | None:
    name = _style_name(para_fmt)
    if name:
        return name
    if node_style and not re.match(r"^a\d+$", str(node_style), re.IGNORECASE):
        return str(node_style)
    return None


_BASE_BOOL_KEYS = ("bold", "italic")
_ADV_BOOL_KEYS = (
    "strike",
    "double_strike",
    "caps",
    "small_caps",
    "superscript",
    "subscript",
    "vanish",
)


def _build_text_format(
    para_fmt: dict[str, Any],
    *,
    node_style: str | None = None,
    run_nodes: list[dict[str, Any]] | None = None,
    merge_runs: bool = True,
    font_context: DocumentFontContext | None = None,
) -> dict[str, Any]:
    """
    Assemble flat ``text_format`` groups (field names aligned with ref).

    ``style_name`` is read for inspection; format migration writers skip it
    (see ``LongDocFormatter.officecli.modify.text_format_scope``).
    """
    run_nodes = run_nodes or []
    if merge_runs and run_nodes:
        main_base_font, main_advanced_font, base_dom, adv_dom = (
            _build_main_font_from_runs(para_fmt, run_nodes, font_context=font_context)
        )
    else:
        base_dom = {
            k: _bool_from_fmt(para_fmt, k) or False for k in _BASE_BOOL_KEYS
        }
        adv_dom = {k: _bool_from_fmt(para_fmt, k) or False for k in _ADV_BOOL_KEYS}
        main_base_font = _apply_explicit_bools(
            _build_base_font(para_fmt), _BASE_BOOL_KEYS, base_dom
        )
        main_base_font = _enrich_base_font_from_para_markup(main_base_font, para_fmt)
        main_base_font = _enrich_base_font_from_theme(
            main_base_font,
            para_fmt,
            run_nodes or [],
            font_context,
        )
        main_advanced_font = _apply_explicit_bools(
            _build_advanced_font(para_fmt), _ADV_BOOL_KEYS, adv_dom
        )
    fmt = dict(para_fmt)

    text_format = _omit_none(
        {
            "style_name": _resolve_style_name(fmt, node_style),
            "base_font": main_base_font or None,
            "mark_rpr_font": _build_mark_rpr_font(para_fmt) or None,
            "advanced_font": main_advanced_font or None,
            "alignment": build_alignment(fmt),
            "outline_level": build_outline_level(fmt),
            "pagination_control": build_pagination_control(fmt),
            "spacing": build_spacing(fmt),
            "indent": build_indent(fmt),
            "list": build_list_info(fmt),
        }
    )

    logical_base = _logical_font_for_diff(main_base_font, _BASE_BOOL_KEYS, base_dom)
    logical_adv = _logical_font_for_diff(
        main_advanced_font, _ADV_BOOL_KEYS, adv_dom
    )
    distinct_runs = _build_distinct_runs(
        run_nodes,
        logical_base,
        logical_adv,
    )
    if distinct_runs:
        text_format["runs"] = distinct_runs
    return text_format


@dataclass
class TextFormatInfo:
    """
    One paragraph's text format (flat groups under ``text_format``).

    Groups (aligned with ref ``text_reader`` categories):

    - ``style_name``
    - ``base_font``, ``advanced_font``
    - ``alignment``, ``outline_level``, ``pagination_control``
    - ``spacing``, ``indent``, ``list``
    - optional ``runs`` when 2+ runs differ from main paragraph font format
    """

    path: str
    text: str
    text_format: dict[str, Any] = field(default_factory=dict)
    paragraph_index: int = 0
    in_table: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraph_index": self.paragraph_index,
            "path": self.path,
            "in_table": self.in_table,
            "text": self.text,
            "text_format": self.text_format,
        }


class WordTextReader:
    """
    Read paragraph text formatting from a .docx via officecli.

    Output is flat and uses officecli native units (``12pt``, ``2cm``, etc.).
    """

    SELECTOR = "paragraph"
    DEFAULT_DEPTH = 2

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")
        self._font_context: DocumentFontContext | None = None

    def get_font_context(self) -> DocumentFontContext:
        """Lazy-load document theme / docDefaults font resolution context."""
        if self._font_context is None:
            self._font_context = DocumentFontContext.load(
                self.doc_path,
                officecli=self.officecli,
            )
        return self._font_context

    def _load_runs_if_needed(self, node: dict[str, Any], *, merge_runs: bool) -> dict[str, Any]:
        if not merge_runs:
            return node
        if node.get("children"):
            return node
        if node.get("childCount", 0) <= 0:
            return node
        path = node.get("path", "")
        if not path:
            return node
        detailed = get_element(
            self.doc_path,
            path,
            officecli=self.officecli,
            depth=self.DEFAULT_DEPTH,
        )
        return detailed or node

    def _node_to_text_info(
        self,
        node: dict[str, Any],
        *,
        paragraph_index: int = 0,
        merge_runs: bool = True,
    ) -> TextFormatInfo:
        path = node.get("path", "")
        node = self._load_runs_if_needed(node, merge_runs=merge_runs)
        para_fmt = dict(node.get("format") or {})
        run_nodes = node.get("children") or []
        return TextFormatInfo(
            path=path,
            text=(node.get("text") or node.get("preview") or "").strip(),
            paragraph_index=paragraph_index,
            in_table=_in_table(path),
            text_format=_build_text_format(
                para_fmt,
                node_style=node.get("style"),
                run_nodes=run_nodes,
                merge_runs=merge_runs,
                font_context=self.get_font_context(),
            ),
        )

    def read_all(
        self,
        *,
        body_only: bool = False,
        merge_runs: bool = True,
        skip_empty: bool = True,
    ) -> list[TextFormatInfo]:
        """
        Read all paragraphs.

        Parameters
        ----------
        body_only:
            Only ``/body/p[...]`` (exclude table/header/footer paragraphs).
        merge_runs:
            Load runs; paragraph ``base_font`` typography from paragraph markup (pPr /
            markRPr); underline from run character majority on ``base_font`` only.
            When 2+ runs differ on bold/italic etc., ``runs`` lists those deltas.
        skip_empty:
            Skip paragraphs with no visible text.
        """
        nodes = query_elements(
            self.doc_path,
            self.SELECTOR,
            officecli=self.officecli,
        )
        if body_only:
            nodes = [n for n in nodes if re.search(r"/body/p(?:\[|$)", n.get("path", ""))]
        body_counter = 0
        results: list[TextFormatInfo] = []
        for node in nodes:
            path = node.get("path", "")
            if body_only:
                body_counter += 1
                p_index = body_counter
            else:
                p_index = _path_index(path, "p") or 0

            text = (node.get("text") or node.get("preview") or "").strip()
            if skip_empty and not text:
                continue

            results.append(
                self._node_to_text_info(
                    node,
                    paragraph_index=p_index,
                    merge_runs=merge_runs,
                )
            )
        return results

    def read_at(
        self,
        path: str,
        *,
        merge_runs: bool = True,
    ) -> TextFormatInfo | None:
        """Read one paragraph by officecli path."""
        node = get_element(
            self.doc_path,
            path,
            officecli=self.officecli,
            depth=self.DEFAULT_DEPTH if merge_runs else 0,
        )
        if not node:
            return None
        p_index = 0
        if "/body/p" in path:
            body_nodes = query_elements(
                self.doc_path,
                "paragraph",
                officecli=self.officecli,
            )
            body_paths = [
                n.get("path")
                for n in body_nodes
                if re.search(r"/body/p(?:\[|$)", n.get("path", ""))
            ]
            if path in body_paths:
                p_index = body_paths.index(path) + 1
        return self._node_to_text_info(
            node,
            paragraph_index=p_index,
            merge_runs=merge_runs,
        )

    def read_by_index(
        self,
        paragraph_index: int,
        *,
        body_only: bool = True,
        merge_runs: bool = True,
    ) -> TextFormatInfo | None:
        """Read one body paragraph by 1-based index (matches ref paragraph_index)."""
        paragraphs = self.read_all(
            body_only=body_only,
            merge_runs=merge_runs,
            skip_empty=False,
        )
        for item in paragraphs:
            if item.paragraph_index == paragraph_index:
                return item
        return None

    def read_by_style(self, style: str, **kwargs: Any) -> list[TextFormatInfo]:
        """Query paragraphs with a given style name or id."""
        selector = f'paragraph[style="{style}"]'
        nodes = query_elements(self.doc_path, selector, officecli=self.officecli)
        return [
            self._node_to_text_info(
                node,
                merge_runs=kwargs.get("merge_runs", True),
            )
            for node in nodes
        ]

    def read_containing(self, text: str, **kwargs: Any) -> list[TextFormatInfo]:
        """Find paragraphs whose text contains the given substring."""
        selector = f'paragraph:contains("{text}")'
        nodes = query_elements(self.doc_path, selector, officecli=self.officecli)
        return [
            self._node_to_text_info(
                node,
                merge_runs=kwargs.get("merge_runs", True),
            )
            for node in nodes
        ]
