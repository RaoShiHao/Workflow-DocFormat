from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = REPO_ROOT

DEFAULT_PROMPT_DIR = PACKAGE_ROOT / "config" / "prompt_config_longdoc"
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "longdoc_config.yaml"
DEFAULT_LLM_CONFIG = PACKAGE_ROOT / "config" / "llm_config.yaml"
EXPERIMENT_CONFIG_PATH = PACKAGE_ROOT / "experiment" / "longdoc_config.yaml"
EXPERIMENT_LLM_CONFIG = PACKAGE_ROOT / "experiment" / "llm_config.yaml"


def resolve_longdoc_config(explicit: str | Path | None = None) -> Path:
    """Experiment yaml if present, else the shared LongDocFormatter config."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path.resolve()
    if EXPERIMENT_CONFIG_PATH.is_file():
        return EXPERIMENT_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def resolve_llm_config(explicit: str | Path | None = None) -> Path:
    """Experiment llm_config.yaml if present, else the shared product file."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path.resolve()
    if EXPERIMENT_LLM_CONFIG.is_file():
        return EXPERIMENT_LLM_CONFIG
    return DEFAULT_LLM_CONFIG
