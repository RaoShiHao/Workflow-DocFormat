"""Resolve list/numbering definitions for cross-document paragraph writes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from LongDocFormatter.officecli.read.format_schema import normalize_num_id
from LongDocFormatter.officecli.read._cli import get_element

from ._cli import OfficeCliError, add_element

_ID_RE = re.compile(r"\[@id=(\d+)\]")


def _parse_added_id(payload: dict[str, Any]) -> int | None:
    for key in ("data", "message"):
        text = str(payload.get(key) or "")
        match = _ID_RE.search(text)
        if match:
            return int(match.group(1))
    return None


def _level_props_from_format(ilvl: int, level_fmt: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    mapping = (
        ("format", "format"),
        ("lvlText", "text"),
        ("start", "start"),
        ("indent", "indent"),
        ("hanging", "hanging"),
        ("justification", "justification"),
    )
    for src_key, dst_suffix in mapping:
        value = level_fmt.get(src_key)
        if value is not None:
            props[f"level{ilvl}.{dst_suffix}"] = value
    return props


class NumberingMapper:
    """
    Map source list definitions to target-document ``numId`` values.

    When ``source_doc`` is given, ``list.num_id`` from the reader is treated as a
    source reference and the full ``abstractNum`` template is cloned into the target.
    When only ``list.num_fmt`` is available, a matching ``num`` instance is created
    on the target (cached by format fingerprint).
    """

    def __init__(
        self,
        target_doc: str | Path,
        *,
        source_doc: str | Path | None = None,
        officecli: str = "officecli",
    ) -> None:
        self.target_doc = Path(target_doc).resolve()
        self.source_doc = Path(source_doc).resolve() if source_doc else None
        self.officecli = officecli
        self._cache: dict[str, int] = {}

    def resolve_list_info(self, list_info: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Return ``list_info`` with a target ``num_id`` when numbering must be cloned."""
        warnings: list[str] = []
        if not list_info:
            return list_info, warnings

        style = list_info.get("list_style")
        if style in ("none", "remove", "clear"):
            return dict(list_info), warnings

        resolved = dict(list_info)
        source_num_id = normalize_num_id(resolved.get("num_id"))
        if source_num_id is None:
            resolved.pop("num_id", None)
        num_fmt = resolved.get("num_fmt")

        if self.source_doc and source_num_id is not None:
            cache_key = f"src:{source_num_id}"
            if cache_key not in self._cache:
                self._cache[cache_key] = self._clone_source_num(int(source_num_id))
            resolved["num_id"] = self._cache[cache_key]
            resolved.pop("list_style", None)
            return resolved, warnings

        if num_fmt is not None and style not in ("none", "remove", "clear"):
            cache_key = f"fmt:{num_fmt}"
            if cache_key not in self._cache:
                self._cache[cache_key] = self._create_num_from_format(
                    str(num_fmt),
                    start=resolved.get("start"),
                )
            resolved["num_id"] = self._cache[cache_key]
            resolved.pop("list_style", None)
            return resolved, warnings

        if source_num_id is not None and not self._target_has_num(int(source_num_id)):
            if num_fmt is not None:
                resolved["num_id"] = self._create_num_from_format(
                    str(num_fmt),
                    start=resolved.get("start"),
                )
                resolved.pop("list_style", None)
                warnings.append(
                    f"Target has no numId={source_num_id}; created new num from num_fmt."
                )
            else:
                warnings.append(
                    f"Target has no numId={source_num_id} and list.num_fmt is missing; "
                    "list binding may fail."
                )

        return resolved, warnings

    def resolve_text_format(
        self,
        text_format: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Resolve ``text_format.list`` in place (copy) before officecli mapping."""
        if not text_format:
            return text_format, []
        list_info = text_format.get("list")
        if not list_info:
            return text_format, []

        resolved_list, warnings = self.resolve_list_info(list_info)
        if resolved_list is list_info:
            return text_format, warnings
        merged = dict(text_format)
        merged["list"] = resolved_list
        return merged, warnings

    def _target_has_num(self, num_id: int) -> bool:
        if num_id <= 0:
            return False
        try:
            node = get_element(
                self.target_doc,
                f"/numbering/num[@id={num_id}]",
                officecli=self.officecli,
            )
        except OfficeCliError:
            return False
        return node is not None

    def _clone_source_num(self, source_num_id: int) -> int:
        if not self.source_doc:
            raise OfficeCliError("source_doc is required to clone numbering definitions.")
        num_node = get_element(
            self.source_doc,
            f"/numbering/num[@id={source_num_id}]",
            officecli=self.officecli,
        )
        if not num_node:
            raise OfficeCliError(
                f"Source document has no /numbering/num[@id={source_num_id}]."
            )
        num_fmt = dict(num_node.get("format") or {})
        abstract_num_id = num_fmt.get("abstractNumId")
        if abstract_num_id is None:
            raise OfficeCliError(
                f"Source num[@id={source_num_id}] has no abstractNumId."
            )
        definition = self._read_abstract_num_definition(int(abstract_num_id))
        target_abstract_id = self._create_abstract_num(definition)
        return self._create_num_instance(target_abstract_id)

    def _read_abstract_num_definition(self, abstract_num_id: int) -> dict[str, Any]:
        if not self.source_doc:
            raise OfficeCliError("source_doc is required to read abstractNum.")
        base = f"/numbering/abstractNum[@id={abstract_num_id}]"
        root = get_element(self.source_doc, base, officecli=self.officecli)
        if not root:
            raise OfficeCliError(
                f"Source document has no {base}."
            )
        root_fmt = dict(root.get("format") or {})
        levels: dict[int, dict[str, Any]] = {}
        for ilvl in range(9):
            level_node = get_element(
                self.source_doc,
                f"{base}/level[{ilvl}]",
                officecli=self.officecli,
            )
            level_fmt = dict((level_node or {}).get("format") or {})
            if level_fmt.get("format") or level_fmt.get("lvlText"):
                levels[ilvl] = level_fmt
        return {
            "type": root_fmt.get("type") or "hybridMultilevel",
            "levels": levels,
        }

    def _create_abstract_num(self, definition: dict[str, Any]) -> int:
        props: dict[str, Any] = {"type": definition.get("type") or "hybridMultilevel"}
        levels = definition.get("levels") or {}
        if not levels:
            props["format"] = "decimal"
            props["text"] = "%1."
        else:
            for ilvl, level_fmt in sorted(levels.items()):
                props.update(_level_props_from_format(int(ilvl), level_fmt))
        payload = add_element(
            self.target_doc,
            "/numbering",
            "abstractNum",
            props,
            officecli=self.officecli,
        )
        abstract_id = _parse_added_id(payload)
        if abstract_id is None:
            raise OfficeCliError(f"Could not parse abstractNum id from: {payload}")
        return abstract_id

    def _create_num_instance(self, abstract_num_id: int) -> int:
        payload = add_element(
            self.target_doc,
            "/numbering",
            "num",
            {"abstractNumId": abstract_num_id},
            officecli=self.officecli,
        )
        num_id = _parse_added_id(payload)
        if num_id is None:
            raise OfficeCliError(f"Could not parse num id from: {payload}")
        return num_id

    def _create_num_from_format(
        self,
        num_fmt: str,
        *,
        start: Any = None,
    ) -> int:
        props: dict[str, Any] = {"format": num_fmt}
        if num_fmt == "bullet":
            props["text"] = "•"
        else:
            props["text"] = "%1."
        if start is not None:
            props["start"] = start
        payload = add_element(
            self.target_doc,
            "/numbering",
            "num",
            props,
            officecli=self.officecli,
        )
        num_id = _parse_added_id(payload)
        if num_id is None:
            raise OfficeCliError(f"Could not parse num id from: {payload}")
        return num_id
