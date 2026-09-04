"""Minimal inventory element. Skill-local copy of workflow DocElement."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Literal, Optional

Layer = Literal[
    "section",
    "paragraph.body",
    "paragraph.table_cell",
    "table",
    "image",
    "run",
]


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
