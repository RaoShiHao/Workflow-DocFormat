"""Tests for target attribute (A) build from text."""

from __future__ import annotations

from LongDocFmt.workflow.contracts import Catalog, CatalogEntry
from LongDocFmt.workflow.declarations import (
    _filter_layer_target_attributes,
    _items_to_decls_all,
    _merge_layer_target_attributes,
    declarations_from_text,
    extract_requirement_delta,
)
from LongDocFmt.workflow.attribute_overlay import merge_attribute_dicts, overlay_target_attributes


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
    calls = {"n": 0}

    class LM:
        model = "mock"

        def chat_json(self, *, system, user, **kwargs):
            calls["n"] += 1
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
                "{{whitelists_by_object}}\n{{roles_json}}\n"
                "{{base_summary}}\n{{text_input}}"
            ),
        },
        llm_kwargs={},
        coverage="sparse",
        base_for_context=base,
    )
    assert calls["n"] == 1
    assert out["ParaBody"]["props"] == {"align": "center"}
    assert "SecCover" not in out


def test_overlay_target_attributes_alias():
    base = {"A": {"object": "paragraph.body", "props": {"size": "12pt"}}}
    patch = {"A": {"object": "paragraph.body", "props": {"align": "center"}}}
    assert overlay_target_attributes(base, patch)["A"]["props"]["size"] == "12pt"


def test_declarations_from_text_one_call_all_objects():
    calls: list[str] = []

    class LM:
        model = "mock"

        def chat_json(self, *, system, user, **kwargs):
            calls.append(user)
            return {"content": json_all_objects()}

    warnings: list[dict] = []
    out = declarations_from_text(
        catalog=_catalog_with_section_and_para(),
        text="format doc",
        language_model=LM(),
        prompt={
            "system": "json",
            "user_template": (
                "{{whitelists_by_object}}\n{{roles_json}}\n{{text_input}}"
            ),
        },
        llm_kwargs={},
        warnings=warnings,
    )
    assert len(calls) == 1
    assert "object=section" in calls[0] or "section" in calls[0]
    assert out["SecCover"]["object"] == "section"
    assert out["SecCover"]["props"].get("marginBottom") == "2cm"
    assert out["ParaBody"]["object"] == "paragraph.body"


def test_declarations_from_text_ignores_unknown_style_ids():
    class LM:
        model = "mock"

        def chat_json(self, *, system, user, **kwargs):
            return {"content": json_paragraph_with_seccover_wrong_and_extra()}

    warnings: list[dict] = []
    out = declarations_from_text(
        catalog=_catalog_with_section_and_para(),
        text="format doc",
        language_model=LM(),
        prompt={
            "system": "json",
            "user_template": "{{whitelists_by_object}}\n{{roles_json}}\n{{text_input}}",
        },
        llm_kwargs={},
        warnings=warnings,
    )
    assert out["SecCover"]["object"] == "section"
    assert out["ParaBody"]["object"] == "paragraph.body"
    assert "Ghost" not in out
    assert any(w.get("kind") == "cross_layer_style_ids_ignored" for w in warnings)


def test_items_to_decls_all_accepts_style_id_map_and_legacy_array():
    from LongDocFmt.workflow import whitelist as wl

    orig = wl.load_whitelist
    wl.load_whitelist = lambda locale="en": {
        "section": {"marginBottom": "length"},
        "paragraph": {"size": "length", "align": "enum"},
    }
    try:
        id_to_object = {"SecCover": "section", "ParaBody": "paragraph.body"}
        compact = _items_to_decls_all(
            {
                "declarations": {
                    "SecCover": {
                        "object": "section",
                        "props": {"marginBottom": "2cm"},
                        "footer": {"align": "center", "size": "9pt"},
                    },
                    "ParaBody": {"object": "paragraph.body", "props": {"size": "12pt"}},
                }
            },
            root_key="declarations",
            id_to_object=id_to_object,
        )
        legacy = _items_to_decls_all(
            {
                "declarations": [
                    {
                        "style_id": "SecCover",
                        "object": "section",
                        "props": {"marginBottom": "2cm"},
                        "footer": {"align": "center", "size": "9pt"},
                    },
                    {"style_id": "ParaBody", "object": "paragraph.body", "props": {"size": "12pt"}},
                ]
            },
            root_key="declarations",
            id_to_object=id_to_object,
        )
        assert compact["SecCover"]["object"] == "section"
        assert compact["SecCover"]["props"].get("marginBottom") == "2cm"
        assert compact["ParaBody"]["object"] == "paragraph.body"
        assert compact["ParaBody"]["props"].get("size") == "12pt"
        assert compact.keys() == legacy.keys()
        patches = _items_to_decls_all(
            {"patches": {"ParaBody": {"object": "paragraph.body", "props": {"align": "center"}}}},
            root_key="patches",
            id_to_object=id_to_object,
        )
        assert patches["ParaBody"]["props"] == {"align": "center"}
        assert _items_to_decls_all({"patches": {}}, root_key="patches", id_to_object=id_to_object) == {}
        assert _items_to_decls_all({"patches": []}, root_key="patches", id_to_object=id_to_object) == {}
    finally:
        wl.load_whitelist = orig


def json_all_objects() -> str:
    import json

    return json.dumps(
        {
            "declarations": [
                {
                    "style_id": "SecCover",
                    "object": "section",
                    "props": {"marginBottom": "2cm"},
                    "footer": {"align": "center", "size": "9pt"},
                },
                {
                    "style_id": "ParaBody",
                    "object": "paragraph.body",
                    "props": {"size": "12pt"},
                },
            ]
        }
    )


def json_paragraph_with_seccover_wrong_and_extra() -> str:
    import json

    return json.dumps(
        {
            "declarations": [
                {
                    "style_id": "SecCover",
                    "object": "section",
                    "props": {"marginBottom": "2cm"},
                    "footer": {"align": "center"},
                },
                {"style_id": "ParaBody", "object": "paragraph.body", "props": {"size": "12pt"}},
                {"style_id": "Ghost", "object": "paragraph.body", "props": {"size": "10pt"}},
            ]
        }
    )
