"""Resolve effective font names from OOXML theme bindings when runs omit explicit rFonts."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ._cli import get_element

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_W = f"{{{W_NS}}}"
_A = f"{{{A_NS}}}"

# w:rFonts theme attribute values → (major|minor scheme, face slot).
_THEME_REF_MAP: dict[str, tuple[str, str]] = {
    "majorHAnsi": ("major", "latin"),
    "minorHAnsi": ("minor", "latin"),
    "majorEastAsia": ("major", "eastAsia"),
    "minorEastAsia": ("minor", "eastAsia"),
    "majorBidi": ("major", "cs"),
    "minorBidi": ("minor", "cs"),
    "majorAscii": ("major", "ascii"),
    "minorAscii": ("minor", "ascii"),
}

# Office theme leaves ea empty; Word picks script-specific fonts for CJK text.
_EAST_ASIA_SCRIPT_FALLBACK = ("Hans", "Hant", "Jpan", "Hang")


@dataclass
class FontSchemeSlots:
    latin: str = ""
    east_asia: str = ""
    cs: str = ""
    ascii: str = ""
    scripts: dict[str, str] = field(default_factory=dict)

    def resolve_face(self, face: str) -> str | None:
        if face == "latin":
            raw = self.latin
        elif face == "eastAsia":
            raw = self.east_asia
        elif face == "cs":
            raw = self.cs
        elif face == "ascii":
            raw = self.ascii or self.latin
        else:
            raw = ""
        if raw:
            return raw
        if face == "eastAsia":
            for script in _EAST_ASIA_SCRIPT_FALLBACK:
                name = self.scripts.get(script)
                if name:
                    return name
        return None


def _parse_font_scheme_element(element: ET.Element) -> FontSchemeSlots:
    slots = FontSchemeSlots()
    for child in element:
        local = child.tag.rsplit("}", 1)[-1]
        if local == "latin":
            slots.latin = child.get("typeface", "") or ""
        elif local == "ea":
            slots.east_asia = child.get("typeface", "") or ""
        elif local == "cs":
            slots.cs = child.get("typeface", "") or ""
        elif local == "font":
            script = child.get("script", "")
            typeface = child.get("typeface", "")
            if script and typeface:
                slots.scripts[script] = typeface
    return slots


def _parse_doc_defaults(styles_xml: bytes) -> dict[str, str]:
    root = ET.fromstring(styles_xml)
    rfonts = root.find(f".//{_W}docDefaults/{_W}rPrDefault/{_W}rPr/{_W}rFonts")
    if rfonts is None:
        return {}
    out: dict[str, str] = {}
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        value = rfonts.get(f"{_W}{attr}") or rfonts.get(attr)
        if value:
            out[attr] = value
    return out


def _parse_theme_font_scheme(theme_xml: bytes) -> tuple[FontSchemeSlots, FontSchemeSlots]:
    root = ET.fromstring(theme_xml)
    font_scheme = root.find(f".//{_A}fontScheme")
    if font_scheme is None:
        return FontSchemeSlots(), FontSchemeSlots()
    major_el = font_scheme.find(f"{_A}majorFont")
    minor_el = font_scheme.find(f"{_A}minorFont")
    major = _parse_font_scheme_element(major_el) if major_el is not None else FontSchemeSlots()
    minor = _parse_font_scheme_element(minor_el) if minor_el is not None else FontSchemeSlots()
    return major, minor


def _officecli_theme_flat(doc_format: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in doc_format.items():
        if not key.startswith("theme.font.") or value is None:
            continue
        text = str(value).strip()
        if text:
            out[key] = text
    return out


@dataclass
class DocumentFontContext:
    """Document-level theme font scheme + docDefaults bindings."""

    major: FontSchemeSlots
    minor: FontSchemeSlots
    defaults: dict[str, str]
    officecli_theme: dict[str, str]

    def resolve_theme_ref(self, theme_ref: str | None) -> str | None:
        if not theme_ref:
            return None
        entry = _THEME_REF_MAP.get(theme_ref)
        if not entry:
            return None
        scheme_key, face = entry
        slots = self.major if scheme_key == "major" else self.minor
        resolved = slots.resolve_face(face)
        if resolved:
            return resolved
        prefix = f"theme.font.{scheme_key}."
        if face == "latin":
            return self.officecli_theme.get(f"{prefix}latin")
        if face == "eastAsia":
            return self.officecli_theme.get(f"{prefix}eastAsia")
        return None

    def default_east_asia_font(self) -> str | None:
        return self.resolve_theme_ref(self.defaults.get("eastAsiaTheme"))

    def default_latin_font(self) -> str | None:
        ref = self.defaults.get("hAnsiTheme") or self.defaults.get("asciiTheme")
        return self.resolve_theme_ref(ref)

    @classmethod
    def load(
        cls,
        doc_path: Path,
        *,
        officecli: str = "officecli",
    ) -> DocumentFontContext:
        doc_path = doc_path.resolve()
        defaults: dict[str, str] = {}
        major = FontSchemeSlots()
        minor = FontSchemeSlots()
        officecli_theme: dict[str, str] = {}

        try:
            with zipfile.ZipFile(doc_path) as archive:
                if "word/styles.xml" in archive.namelist():
                    defaults = _parse_doc_defaults(archive.read("word/styles.xml"))
                theme_name = next(
                    (n for n in archive.namelist() if n.startswith("word/theme/theme") and n.endswith(".xml")),
                    None,
                )
                if theme_name:
                    major, minor = _parse_theme_font_scheme(archive.read(theme_name))
        except (OSError, zipfile.BadZipFile, ET.ParseError):
            pass

        try:
            root = get_element(doc_path, "/", officecli=officecli, depth=0)
            if root:
                officecli_theme = _officecli_theme_flat(dict(root.get("format") or {}))
        except Exception:
            pass

        return cls(
            major=major,
            minor=minor,
            defaults=defaults,
            officecli_theme=officecli_theme,
        )
