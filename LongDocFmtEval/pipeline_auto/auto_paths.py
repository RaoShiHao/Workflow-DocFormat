"""Paths for pipeline_auto.

**Primary output** is ``datasets/{id}_{lang}_{topic_name}/`` (see ``topic_layout``).
``work/`` holds legacy flat mirrors and scratch (step0 suggestions, old topics).

LLM / image credentials: always use ``CONFIG_DIR`` yaml via ``auto_llm``
(``llm_config.yaml`` / ``role_library_llm_config.yaml`` / ``seed_llm_config.yaml``).
Do not rely on ``common.paths`` defaults (those point at ``pipeline_role``).
"""
from __future__ import annotations

import re
from pathlib import Path

from LongDocFmtEval.common.prompt_locale import resolve_prompt_path as _resolve_prompt_path

COMMON_ROOT = Path(__file__).resolve().parent.parent / "common"
AUTODATA_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = AUTODATA_ROOT.parent

PIPELINE_AUTO_ROOT = AUTODATA_ROOT / "pipeline_auto"
PIPELINE_ROLE_ROOT = PIPELINE_AUTO_ROOT  # alias so copied code keeps working

CONFIG_DIR = PIPELINE_AUTO_ROOT / "config"
LLM_CONFIG_PATH = CONFIG_DIR / "llm_config.yaml"
OFFICECLI_WHITELIST_PATH = CONFIG_DIR / "officecli_whitelist.yaml"  # zh default; en → officecli_whitelist_en.yaml


def officecli_locale_yaml(name: str, locale: str = "zh") -> Path:
    """Resolve officecli_*.yaml by locale: en* → ``*_en.yaml`` if present, else Chinese default."""
    base = CONFIG_DIR / name
    if str(locale).lower().startswith("en"):
        en = CONFIG_DIR / name.replace(".yaml", "_en.yaml")
        if en.exists():
            return en
    return base


def prompt_path(locale: str, name: str, *, fallbacks: tuple[str, ...] = ()) -> Path:
    """``prompt_config/`` (zh) or ``prompt_config_en/`` (en) under pipeline_auto config."""
    return _resolve_prompt_path(CONFIG_DIR, locale, name, fallbacks=fallbacks)


# Primary dataset tree（编排 / --id 默认产物）
DATASETS_DIR = PIPELINE_AUTO_ROOT / "datasets"
# Legacy / scratch under work/
WORK_DIR = PIPELINE_AUTO_ROOT / "work"
GENRE_SUGGESTIONS_DIR = WORK_DIR / "step0_genre_suggestions"  # pre-topic; not under datasets/
# Flat mirrors（无 --id / 显式路径时的兼容落盘；编排以 datasets 为准）
ROLE_LIBRARY_DRAFTS_DIR = WORK_DIR / "step1_role_library_drafts"
THEMES_DIR = WORK_DIR / "step2_themes"
OUTLINES_DIR = WORK_DIR / "step3_outlines"
TEMPLATES_DIR = WORK_DIR / "step4_templates"
SEEDS_DIR = WORK_DIR / "step4_seeds_unused"
DOCUMENTS_DIR = WORK_DIR / "step5_documents"
PAIRS_DIR = WORK_DIR / "step6_pairs"
TOPICS_DIR = WORK_DIR / "topics"  # legacy topic tree only

# Legacy flat-dir aliases (pre-renumber)
LEGACY_OUTLINES_DIR = WORK_DIR / "step2_outlines"
LEGACY_TEMPLATES_DIR = WORK_DIR / "step3_templates"

DOCS_DIR = PIPELINE_AUTO_ROOT / "docs"
SEED_PKG_DIR = PIPELINE_AUTO_ROOT / "seed"
REFS_DIR = AUTODATA_ROOT / "refs"
PROBES_DIR = AUTODATA_ROOT / "probes"

SEED_LLM_CONFIG_PATH = CONFIG_DIR / "seed_llm_config.yaml"
if not SEED_LLM_CONFIG_PATH.exists():
    SEED_LLM_CONFIG_PATH = LLM_CONFIG_PATH

ROLE_LIBRARY_LLM_CONFIG_PATH = CONFIG_DIR / "role_library_llm_config.yaml"

PIPELINE_SEEDGEN_ROOT = PIPELINE_AUTO_ROOT
PIPELINE_SEED_ROOT = PIPELINE_AUTO_ROOT
SEEDGEN_CONFIG_DIR = CONFIG_DIR
SEEDGEN_LLM_CONFIG_PATH = SEED_LLM_CONFIG_PATH
SEEDGEN_WORK_DIR = WORK_DIR
SEEDGEN_SEEDS_DIR = SEEDS_DIR
SEED_CONFIG_DIR = CONFIG_DIR
SEED_WORK_DIR = WORK_DIR
SEED_SEEDS_DIR = SEEDS_DIR

# Amplify defaults（C1/C2/C3 各一 → theme=3；每 theme 一份大纲 → outline=1）
DEFAULT_THEME_COUNT = 3          # step2 写入 themes.xlsx：默认 C1+C2+C3
DEFAULT_RUN_THEME_COUNT = 3      # 编排默认跑完全部 complexity 档
DEFAULT_OUTLINE_COUNT = 1
DEFAULT_DOCUMENT_COUNT = 1
DEFAULT_CHAPTER_WORKERS = 2      # step5 specimen / step6 分章写作并发（样本内）

# datasets/{id}/C1/stepN_* — one canonical owner per shared filename.
_COMPLEXITY_NAMES = ("C1", "C2", "C3")
_KIND_DIRS: dict[str, tuple[str, ...]] = {
    "outlines": ("step3_outlines", "outline"),
    "styles": ("step4_styles", "step4_template"),
    "template": ("step5_template", "step4_template", "template"),
    "document": ("step6_document", "step5_document", "document"),
    "pairs": ("step7_pairs", "step6_pairs", "pairs"),
}
# Files that must exist only in the owner step folder (later steps read by path).
_OWNED_BY: dict[str, str] = {
    "plan.json": "outlines",
    "selected_library.yaml": "outlines",
    "stylesheet.json": "outlines",
    "outline_tree.md": "outlines",
    "selected_tree.md": "outlines",
    "writing_outline.json": "outlines",
    "writing_outline.md": "outlines",
    "writing_stylesheet.json": "outlines",
    "template.json": "styles",
    "role_params.json": "styles",
    "template.md": "styles",
    "style_layers.json": "styles",
    "validation.json": "styles",
    "raw.txt": "styles",
    "source.json": "styles",
    "template.docx": "template",
    "preview.docx": "template",
    "style_tree.json": "template",
    "brief.json": "styles",
    "outline.json": "template",
    "assets_preview.json": "template",
    "exhibit_stats.json": "template",
    "specimen_plan.json": "template",
}
# Gold render may rewrite these next to document.docx; otherwise reuse step5 template.
_LOCAL_THEN_TEMPLATE = frozenset({"map.json", "styles.json", "tables.json"})

# Per-chapter writing blocks live under step3_outlines/blocks/ (not the outlines root).
STEP3_BLOCKS_DIRNAME = "blocks"
_LEGACY_BLOCK_JSON = re.compile(r"^blocks_(I\d+)\.json$", re.IGNORECASE)
_LEGACY_BLOCK_RAW = re.compile(r"^raw_blocks_(I\d+)(\.attempt1)?\.txt$", re.IGNORECASE)


def step3_blocks_dir(outline_dir: Path, *, mkdir: bool = False) -> Path:
    """``C*/step3_outlines/blocks/``."""
    d = Path(outline_dir) / STEP3_BLOCKS_DIRNAME
    if mkdir:
        d.mkdir(parents=True, exist_ok=True)
    return d


def step3_block_json_path(outline_dir: Path, instance_id: str, *, mkdir: bool = False) -> Path:
    return step3_blocks_dir(outline_dir, mkdir=mkdir) / f"{instance_id}.json"


def step3_block_raw_path(outline_dir: Path, instance_id: str, *, mkdir: bool = False) -> Path:
    return step3_blocks_dir(outline_dir, mkdir=mkdir) / f"{instance_id}.raw.txt"


def step3_block_attempt_path(outline_dir: Path, instance_id: str, *, mkdir: bool = False) -> Path:
    return step3_blocks_dir(outline_dir, mkdir=mkdir) / f"{instance_id}.attempt1.txt"


def step3_chunks_path(outline_dir: Path, *, mkdir: bool = False) -> Path:
    return step3_blocks_dir(outline_dir, mkdir=mkdir) / "chunks.json"


def resolve_step3_block_json(outline_dir: Path, instance_id: str) -> Path | None:
    """Canonical ``blocks/I01.json``, then legacy names in that folder or the outlines root."""
    outline_dir = Path(outline_dir)
    iid = str(instance_id or "").strip()
    if not iid:
        return None
    d = step3_blocks_dir(outline_dir)
    for p in (
        d / f"{iid}.json",
        d / f"blocks_{iid}.json",
        outline_dir / f"blocks_{iid}.json",
    ):
        if p.is_file():
            return p
    return None


def resolve_step3_block_raw(outline_dir: Path, instance_id: str) -> Path | None:
    outline_dir = Path(outline_dir)
    iid = str(instance_id or "").strip()
    d = step3_blocks_dir(outline_dir)
    for p in (
        d / f"{iid}.raw.txt",
        d / f"raw_blocks_{iid}.txt",
        outline_dir / f"raw_blocks_{iid}.txt",
    ):
        if p.is_file():
            return p
    return None


def resolve_step3_chunks(outline_dir: Path) -> Path | None:
    outline_dir = Path(outline_dir)
    for p in (
        step3_chunks_path(outline_dir),
        outline_dir / "outline_chunks.json",
    ):
        if p.is_file():
            return p
    return None


def migrate_step3_block_files(outline_dir: Path) -> list[str]:
    """Move legacy ``blocks_Ixx.json`` / ``raw_blocks_*`` / ``outline_chunks.json`` into ``blocks/``."""
    outline_dir = Path(outline_dir)
    notes: list[str] = []
    if not outline_dir.is_dir():
        return notes
    dest = step3_blocks_dir(outline_dir)

    def _move(src: Path, target: Path) -> None:
        if src.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            src.unlink()
            notes.append(f"removed duplicate {src.name} (kept {target.relative_to(outline_dir)})")
            return
        src.replace(target)
        notes.append(f"{src.name} → {target.relative_to(outline_dir).as_posix()}")

    for p in list(outline_dir.iterdir()):
        if not p.is_file():
            continue
        m = _LEGACY_BLOCK_JSON.fullmatch(p.name)
        if m:
            _move(p, dest / f"{m.group(1)}.json")
            continue
        m = _LEGACY_BLOCK_RAW.fullmatch(p.name)
        if m:
            iid = m.group(1)
            suffix = ".attempt1.txt" if m.group(2) else ".raw.txt"
            _move(p, dest / f"{iid}{suffix}")
            continue
        if p.name == "outline_chunks.json":
            _move(p, dest / "chunks.json")

    # If files were already copied into blocks/ with old names, rename in place.
    if dest.is_dir():
        for p in list(dest.iterdir()):
            if not p.is_file():
                continue
            m = _LEGACY_BLOCK_JSON.fullmatch(p.name)
            if m:
                _move(p, dest / f"{m.group(1)}.json")
                continue
            m = _LEGACY_BLOCK_RAW.fullmatch(p.name)
            if m:
                iid = m.group(1)
                suffix = ".attempt1.txt" if m.group(2) else ".raw.txt"
                _move(p, dest / f"{iid}{suffix}")
    return notes


def complexity_root_of(work_dir: Path) -> Path | None:
    """``C1/`` if ``work_dir`` is ``…/C1/step4_styles`` (or the C* folder itself)."""
    p = Path(work_dir).resolve()
    for _ in range(5):
        if p.name in _COMPLEXITY_NAMES:
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def step_kind_of(work_dir: Path) -> str | None:
    name = Path(work_dir).name
    for kind, names in _KIND_DIRS.items():
        if name in names:
            return kind
    return None


def sibling_step_dir(work_dir: Path, kind: str) -> Path | None:
    root = complexity_root_of(work_dir)
    if root is None or kind not in _KIND_DIRS:
        return None
    nonempty: list[Path] = []
    for dirname in _KIND_DIRS[kind]:
        d = root / dirname
        if d.is_dir():
            try:
                next(d.iterdir())
            except StopIteration:
                continue
            nonempty.append(d)
    if nonempty:
        return nonempty[0]
    return root / _KIND_DIRS[kind][0]


def _meta_dir(work_dir: Path, meta_name: str, *keys: str) -> Path | None:
    meta_path = Path(work_dir) / meta_name
    if not meta_path.is_file():
        return None
    try:
        import json  # noqa: PLC0415

        meta = json.loads(meta_path.read_text(encoding="utf-8")) or {}
        for key in keys:
            raw = str(meta.get(key) or "").strip()
            if not raw:
                continue
            p = Path(raw)
            if p.is_dir():
                return p
            if p.is_file():
                return p.parent
    except Exception:  # noqa: BLE001
        return None
    return None


def resolve_work_file(work_dir: Path, name: str) -> Path:
    """Find a shared artifact without copying it into every step folder.

    Prefer the canonical owner (``C*/step3_outlines`` / ``step4_styles`` / ``step5_template``).
    ``map.json`` / ``styles.json`` / ``tables.json`` stay local when gold wrote them.
    """
    work_dir = Path(work_dir)
    local = work_dir / name
    owner = _OWNED_BY.get(name)
    here = step_kind_of(work_dir)

    if name in _LOCAL_THEN_TEMPLATE and local.is_file():
        return local

    if owner:
        sib = sibling_step_dir(work_dir, owner)
        if sib is not None:
            cand = sib / name
            if cand.is_file():
                return cand
        if here == owner and local.is_file():
            return local

    if local.is_file():
        return local

    td = _meta_dir(work_dir, "source_step5.json", "styles_dir", "template_dir")
    if td is not None:
        cand = td / name
        if cand.is_file():
            return cand
        nested = resolve_work_file(td, name) if td.resolve() != work_dir.resolve() else None
        if nested is not None and nested.is_file():
            return nested

    s6 = _meta_dir(work_dir, "source_step6.json", "template_dir", "styles_dir", "step5_dir")
    if s6 is not None and s6.resolve() != work_dir.resolve():
        cand = resolve_work_file(s6, name)
        if cand.is_file():
            return cand

    s7 = _meta_dir(work_dir, "source_step7.json", "document_dir", "step6_dir", "step5_dir")
    if s7 is not None and s7.resolve() != work_dir.resolve():
        cand = resolve_work_file(s7, name)
        if cand.is_file():
            return cand

    if owner == "outlines" or name in ("plan.json", "selected_library.yaml", "stylesheet.json"):
        for root in (OUTLINES_DIR, LEGACY_OUTLINES_DIR):
            cand = root / work_dir.name / name
            if cand.is_file():
                return cand
        return OUTLINES_DIR / work_dir.name / name
    if owner in ("template", "styles") or name in _LOCAL_THEN_TEMPLATE:
        for root in (TEMPLATES_DIR, LEGACY_TEMPLATES_DIR):
            cand = root / work_dir.name / name
            if cand.is_file():
                return cand
        return TEMPLATES_DIR / work_dir.name / name
    return local


def resolve_outline_sidecar(work_dir: Path, name: str) -> Path:
    """Locate plan.json / selected_library.yaml / stylesheet.json for a work dir."""
    return resolve_work_file(work_dir, name)


def resolve_asset_file(work_dir: Path, rel: str | None) -> Path | None:
    """Resolve ``assets/F3.png`` or ``../step5_template/assets_preview/F3.png``."""
    if not rel:
        return None
    work_dir = Path(work_dir)
    rel_path = Path(str(rel))
    cand = rel_path if rel_path.is_absolute() else (work_dir / rel_path)
    if cand.is_file():
        return cand
    name = rel_path.name
    search: list[Path] = [
        work_dir / "assets" / name,
        work_dir / "assets_preview" / name,
    ]
    for kind, sub in (
        ("document", "assets"),
        ("template", "assets"),
        ("template", "assets_preview"),
    ):
        d = sibling_step_dir(work_dir, kind)
        if d is not None:
            search.append(d / sub / name)
    td = _meta_dir(work_dir, "source_step5.json", "styles_dir", "template_dir")
    if td is not None:
        search.append(td / "assets_preview" / name)
        search.append(td / "assets" / name)
    td6 = _meta_dir(work_dir, "source_step6.json", "template_dir", "styles_dir")
    if td6 is not None:
        search.append(td6 / "assets_preview" / name)
        search.append(td6 / "assets" / name)
    for p in search:
        if p.is_file():
            return p
    return None


def drop_redundant_copies(work_dir: Path) -> list[str]:
    """Unlink shared files that already live in their owner step folder."""
    work_dir = Path(work_dir)
    here = step_kind_of(work_dir)
    dropped: list[str] = []
    for name, owner in _OWNED_BY.items():
        if owner == here:
            continue
        local = work_dir / name
        if not local.is_file():
            continue
        canonical = resolve_work_file(work_dir, name)
        try:
            if not canonical.is_file() or canonical.resolve() == local.resolve():
                continue
            local.unlink()
            dropped.append(name)
        except OSError:
            continue
    return dropped


def rel_from(path: Path, dest_dir: Path) -> str:
    """POSIX relative path from ``dest_dir`` to ``path``."""
    import os  # noqa: PLC0415

    return Path(os.path.relpath(path, dest_dir)).as_posix()
