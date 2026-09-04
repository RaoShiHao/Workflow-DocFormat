"""Tests for target attribute (A) build from text."""

from __future__ import annotations

from LongDocFormatter.workflow.contracts import Catalog, CatalogEntry
from LongDocFormatter.workflow.declarations import (
    _filter_layer_target_attributes,
    _merge_layer_target_attributes,
    declarations_from_text,
    extract_requirement_delta,
)
from LongDocFormatter.workflow.attribute_overlay import merge_attribute_dicts, overlay_target_attributes


def _catalog_with_section_and_para() -> Catalog:
    return Catalog(
        entries=[
            CatalogEntry(
                style_id="SecCover",
                object="section",
                display_name="Cover",
                description="Cover section",
            ),
            CatalogEntry(
                style_id="ParaBody",
                object="paragraph.body",
                display_name="Body",
                description="Body text",
            ),
        ]
    )


def test_filter_drops_cross_layer_style_ids():
    allowed = {"ParaBody"}
    raw = {
        "ParaBody": {"object": "paragraph.body", "props": {"size": "12pt"}},
        "SecCover": {"object": "paragraph.body", "props": {}},
    }
    kept, ignored = _filter_layer_target_attributes(raw, allowed)
    assert list(kept.keys()) == ["ParaBody"]
    assert ignored == ["SecCover"]


def test_merge_does_not_overwrite_section_with_paragraph():
    existing = {
        "SecCover": {
            "object": "section",
            "props": {"marginBottom": "2cm"},
            "footer": {"align": "center"},
        }
    }
    incoming = {"SecCover": {"object": "paragraph.body", "props": {}}}
    _merge_layer_target_attributes(existing, incoming, layer="paragraph.body")
    assert existing["SecCover"]["object"] == "section"
    assert existing["SecCover"]["footer"]["align"] == "center"


def test_merge_attribute_dicts_preserves_base_object_on_conflict():
    base = {"SecCover": {"object": "section", "props": {"marginBottom": "2cm"}}}
    overlay = {"SecCover": {"object": "paragraph.body", "props": {"size": "12pt"}}}
    merged = merge_attribute_dicts(base, overlay)
    assert merged["SecCover"]["object"] == "section"
    assert merged["SecCover"]["props"]["marginBottom"] == "2cm"
    assert "size" not in merged["SecCover"]["props"]


def test_merge_attribute_dicts_merges_section_header_without_dropping_footer():
    base = {
        "SecBody": {
            "object": "section",
            "props": {"marginBottom": "2.5cm"},
            "footer": {"field": "page", "align": "center"},
        }
    }
    overlay = {
        "SecBody": {
            "object": "section",
            "header": {"text": "Business Review", "align": "center"},
        }
    }
    merged = merge_attribute_dicts(base, overlay)
    assert merged["SecBody"]["props"]["marginBottom"] == "2.5cm"
    assert merged["SecBody"]["header"]["text"] == "Business Review"
    assert merged["SecBody"]["footer"]["field"] == "page"


def test_merge_attribute_dicts_sparse_patch_keeps_base_font():
    base = {
        "ParaHeading1": {
            "object": "paragraph.body",
            "props": {"align": "left", "font.latin": "Arial", "size": "16pt"},
        }
    }
    overlay = {"ParaHeading1": {"object": "paragraph.body", "props": {"align": "center"}}}
    merged = merge_attribute_dicts(base, overlay)
    assert merged["ParaHeading1"]["props"]["align"] == "center"
    assert merged["ParaHeading1"]["props"]["font.latin"] == "Arial"
    assert merged["ParaHeading1"]["props"]["size"] == "16pt"


def test_extract_requirement_delta_prefers_delta_section():
    text = (
        "# Intro\nSome context.\n\n"
        "## Adjustments relative to the template\n"
        "- Heading 1: center\n\n"
        "## Other\nIgnore me."
    )
    delta = extract_requirement_delta(text)
    assert "Heading 1" in delta
    assert "Ignore me" not in delta


def test_declarations_from_text_sparse_coverage():
    class LM:
        model = "mock"

        def chat_json(self, *, system, user, **kwargs):
            if "paragraph.body" in user:
                import json

                return {
                    "content": json.dumps(
                        {
                            "patches": [
                                {
                                    "style_id": "ParaBody",
                                    "object": "paragraph.body",
                                    "props": {"align": "center"},
                                }
                            ]
                        }
                    )
                }
            return {"content": '{"patches": []}'}

    base = {
        "ParaBody": {
            "object": "paragraph.body",
            "props": {"font.latin": "Arial", "size": "12pt", "align": "justify"},
        }
    }
    out = declarations_from_text(
        catalog=_catalog_with_section_and_para(),
        text="## Adjustments relative to the template\nCenter body text.",
        language_model=LM(),
        prompt={
            "system": "json",
            "user_template": (
                "{{element_type}}\n{{whitelist}}\n{{roles_json}}\n"
                "{{base_summary}}\n{{text_input}}"
            ),
        },
        llm_kwargs={},
        coverage="sparse",
        base_for_context=base,
    )
    assert out["ParaBody"]["props"] == {"align": "center"}
    assert "SecCover" not in out


def test_overlay_target_attributes_alias():
    base = {"A": {"object": "paragraph.body", "props": {"size": "12pt"}}}
    patch = {"A": {"object": "paragraph.body", "props": {"align": "center"}}}
    assert overlay_target_attributes(base, patch)["A"]["props"]["size"] == "12pt"


def test_declarations_from_text_ignores_cross_layer_llm_ids():
    calls: list[str] = []

    class LM:
        model = "mock"

        def chat_json(self, *, system, user, **kwargs):
            if "Element type: section" in user or "元素类型: section" in user:
                calls.append("section")
                return {
                    "content": json_section(),
                }
            calls.append("paragraph")
            return {"content": json_paragraph_with_seccover()}

    warnings: list[dict] = []
    out = declarations_from_text(
        catalog=_catalog_with_section_and_para(),
        text="format doc",
        language_model=LM(),
        prompt={
            "system": "json",
            "user_template": (
                "Element type: {{element_type}}\n{{whitelist}}\n{{roles_json}}\n{{text_input}}"
            ),
        },
        llm_kwargs={},
        warnings=warnings,
    )
    assert out["SecCover"]["object"] == "section"
    assert out["SecCover"]["props"].get("marginBottom") == "2cm"
    assert out["ParaBody"]["object"] == "paragraph.body"
    assert any(w.get("kind") == "cross_layer_style_ids_ignored" for w in warnings)


def json_section() -> str:
    import json

    return json.dumps(
        {
            "declarations": [
                {
                    "style_id": "SecCover",
                    "object": "section",
                    "props": {"marginBottom": "2cm"},
                    "footer": {"align": "center", "size": "9pt"},
                }
            ]
        }
    )


def json_paragraph_with_seccover() -> str:
    import json

    return json.dumps(
        {
            "declarations": [
                {"style_id": "ParaBody", "object": "paragraph.body", "props": {"size": "12pt"}},
                {"style_id": "SecCover", "object": "paragraph.body", "props": {}},
            ]
        }
    )
