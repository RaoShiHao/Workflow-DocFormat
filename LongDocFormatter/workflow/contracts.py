from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


Layer = Literal[
    "section",
    "paragraph.body",
    "paragraph.table_cell",
    "table",
    "image",
    "run",
]

LAYER_ORDER: tuple[Layer, ...] = (
    "section",
    "paragraph.body",
    "paragraph.table_cell",
    "table",
    "image",
    "run",
)

InputMode = Literal["template", "template_w_text", "text_requirement"]


@dataclass
class CatalogEntry:
    """One formatting target (T): a named role with optional introduction text."""

    style_id: str  # target_id, e.g. ParaHeading1 / Cover
    object: Layer
    display_name: str
    description: str
    exemplar_path: str = ""
    exemplar_location_id: Any = None
    typical_sections: List[str] = field(default_factory=list)
    caption_type: str = ""
    header_semantics: str = ""
    captions: List[str] = field(default_factory=list)
    header_rows: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if not payload.get("caption_type"):
            payload.pop("caption_type", None)
        if not payload.get("header_semantics"):
            payload.pop("header_semantics", None)
        if not payload.get("captions"):
            payload.pop("captions", None)
        if not payload.get("header_rows"):
            payload.pop("header_rows", None)
        return payload


@dataclass
class Catalog:
    """Target set T: catalog of formatting targets across layers."""

    entries: List[CatalogEntry] = field(default_factory=list)

    def by_layer(self, layer: Layer) -> List[CatalogEntry]:
        return [e for e in self.entries if e.object == layer]

    def to_dict(self) -> Dict[str, Any]:
        return {"entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "Catalog":
        items = (data or {}).get("entries") or []
        entries = []
        for it in items:
            if not isinstance(it, dict):
                continue
            obj = str(it.get("object") or "paragraph.body")
            entries.append(
                CatalogEntry(
                    style_id=str(it.get("style_id") or ""),
                    object=obj,  # type: ignore[arg-type]
                    display_name=str(it.get("display_name") or it.get("style_id") or ""),
                    description=str(it.get("description") or ""),
                    exemplar_path=str(it.get("exemplar_path") or ""),
                    exemplar_location_id=it.get("exemplar_location_id"),
                    typical_sections=[
                        str(x) for x in (it.get("typical_sections") or []) if str(x).strip()
                    ],
                    caption_type=str(it.get("caption_type") or "").strip(),
                    header_semantics=str(it.get("header_semantics") or "").strip(),
                    captions=[
                        str(x).strip() for x in (it.get("captions") or []) if str(x).strip()
                    ],
                    header_rows=[
                        str(x).strip() for x in (it.get("header_rows") or []) if str(x).strip()
                    ],
                )
            )
        return cls(entries=entries)


@dataclass
class DocElement:
    """One instance in a document (init or template)."""

    layer: Layer
    location_id: Any
    path: str
    props: Dict[str, Any]
    content: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    image_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "DocElement":
        d = data or {}
        return cls(
            layer=str(d.get("layer") or "paragraph.body"),  # type: ignore[arg-type]
            location_id=d.get("location_id"),
            path=str(d.get("path") or ""),
            props=dict(d.get("props") or {}),
            content=str(d.get("content") or ""),
            meta=dict(d.get("meta") or {}),
            image_path=d.get("image_path"),
        )


@dataclass
class Assignment:
    """Element-to-target map M: location_id → target_id (+ table cells / runs)."""

    by_layer: Dict[str, Dict[str, str]] = field(default_factory=dict)
    table_cells: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    paragraph_runs: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True when there is nothing to apply in document_modify."""
        if any(bool(m) for m in (self.by_layer or {}).values()):
            return False
        if self.table_cells:
            return False
        if self.paragraph_runs:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "by_layer": self.by_layer,
            "table_cells": self.table_cells,
            "paragraph_runs": self.paragraph_runs,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "Assignment":
        d = data or {}
        return cls(
            by_layer=dict(d.get("by_layer") or {}),
            table_cells=dict(d.get("table_cells") or {}),
            paragraph_runs=dict(d.get("paragraph_runs") or {}),
        )
