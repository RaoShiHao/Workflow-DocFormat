"""Bootstrap repo root on sys.path for errors_clean CLI scripts."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PIPELINE_AUTO = _REPO_ROOT / "LongDocFmtEval" / "pipeline_auto"


def default_datasets_dir() -> Path:
    for candidate in (
        PIPELINE_AUTO / "datasets",
        Path.cwd() / "LongDocFmtEval" / "pipeline_auto" / "datasets",
        Path.cwd() / "datasets",
    ):
        if candidate.is_dir():
            return candidate
    return PIPELINE_AUTO / "datasets"
