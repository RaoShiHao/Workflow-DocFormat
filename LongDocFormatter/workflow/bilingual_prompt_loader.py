from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _pick_locale(block: Dict[str, Any], locale: str) -> Dict[str, str]:
    preferred = "en" if str(locale).lower().startswith("en") else "zh"
    other = "zh" if preferred == "en" else "en"
    for loc in (preferred, other):
        if loc in block and isinstance(block[loc], dict):
            return {
                "system": str(block[loc].get("system") or ""),
                "user_template": str(block[loc].get("user_template") or ""),
            }
    if "system" in block:
        return {
            "system": str(block.get("system") or ""),
            "user_template": str(block.get("user_template") or ""),
        }
    return {"system": "", "user_template": ""}


class PromptLoader:
    def __init__(self, prompt_dir: str | Path):
        self.prompt_dir = Path(prompt_dir)

    def load(self, name: str, *, locale: str = "en", section: str = "element_classification") -> Dict[str, str]:
        path = self.prompt_dir / f"{name}.yaml"
        if not path.exists():
            alias = {
                "paragraph.body": "paragraph_body",
                "paragraph.table_cell": "paragraph_cell",
            }.get(name, name)
            path = self.prompt_dir / f"{alias}.yaml"
        data = _load_yaml(path) if path.exists() else {}
        block = data.get(section) or {}
        return _pick_locale(block, locale)
