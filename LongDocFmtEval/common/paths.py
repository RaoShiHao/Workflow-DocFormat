from __future__ import annotations

from pathlib import Path

COMMON_ROOT = Path(__file__).resolve().parent
AUTODATA_ROOT = COMMON_ROOT.parent
REPO_ROOT = AUTODATA_ROOT.parent

# Single pipeline under LongDocFmtEval/
PIPELINE_ROLE_ROOT = AUTODATA_ROOT / "pipeline_role"

# --- Role + Seed (unified) ---
CONFIG_DIR = PIPELINE_ROLE_ROOT / "config"
LLM_CONFIG_PATH = CONFIG_DIR / "llm_config.yaml"
OFFICECLI_WHITELIST_PATH = CONFIG_DIR / "officecli_whitelist.yaml"  # zh default; en → officecli_whitelist_en.yaml


def officecli_locale_yaml(name: str, locale: str = "zh") -> Path:
    """Resolve officecli_*.yaml by locale: en* → ``*_en.yaml`` if present, else Chinese default.

    ``name`` examples: ``officecli_whitelist.yaml``, ``officecli_prop_labels.yaml``.
    """
    base = CONFIG_DIR / name
    if str(locale).lower().startswith("en"):
        en = CONFIG_DIR / name.replace(".yaml", "_en.yaml")
        if en.exists():
            return en
    return base

WORK_DIR = PIPELINE_ROLE_ROOT / "work"
# Step-prefixed work roots (easy to see which pipeline stage wrote the artifacts)
GENRE_SUGGESTIONS_DIR = WORK_DIR / "step0_genre_suggestions"
ROLE_LIBRARY_DRAFTS_DIR = WORK_DIR / "step1_role_library_drafts"
OUTLINES_DIR = WORK_DIR / "step2_outlines"
TEMPLATES_DIR = WORK_DIR / "step3_templates"
SEEDS_DIR = WORK_DIR / "step4_seeds"
DOCUMENTS_DIR = WORK_DIR / "step5_documents"
PAIRS_DIR = WORK_DIR / "step6_pairs"

DOCS_DIR = PIPELINE_ROLE_ROOT / "docs"
SEED_PKG_DIR = PIPELINE_ROLE_ROOT / "seed"
REFS_DIR = AUTODATA_ROOT / "refs"
PROBES_DIR = AUTODATA_ROOT / "probes"

# Seed tooling (same LLM as role by default; optional override file)
SEED_LLM_CONFIG_PATH = CONFIG_DIR / "seed_llm_config.yaml"
if not SEED_LLM_CONFIG_PATH.exists():
    SEED_LLM_CONFIG_PATH = LLM_CONFIG_PATH

# Step1 Role 库专用（长 JSON）；文件不存在或未填 key 时回退 llm_config.yaml
ROLE_LIBRARY_LLM_CONFIG_PATH = CONFIG_DIR / "role_library_llm_config.yaml"

# Back-compat aliases (old pipeline_seedgen names)
PIPELINE_SEEDGEN_ROOT = PIPELINE_ROLE_ROOT
PIPELINE_SEED_ROOT = PIPELINE_ROLE_ROOT
SEEDGEN_CONFIG_DIR = CONFIG_DIR
SEEDGEN_LLM_CONFIG_PATH = SEED_LLM_CONFIG_PATH
SEEDGEN_WORK_DIR = WORK_DIR
SEEDGEN_SEEDS_DIR = SEEDS_DIR
SEED_CONFIG_DIR = CONFIG_DIR
SEED_WORK_DIR = WORK_DIR
SEED_SEEDS_DIR = SEEDS_DIR
