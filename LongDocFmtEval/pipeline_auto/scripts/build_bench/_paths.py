"""Path / id helpers for build_bench."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from LongDocFmtEval.common.draft_resolve import parse_ids_arg  # noqa: E402
from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR, DATASETS_DIR  # noqa: E402

DEFAULT_IDS_FILE = CONFIG_DIR / "ids.txt"
DEFAULT_OUT_ROOT = Path(r"D:\LongDocFmtEval")


def load_ids_from_file(path: Path) -> list[int]:
    """Parse topic ids from a text file (comma lists, ``#`` comments, blank lines)."""
    if not path.is_file():
        raise FileNotFoundError(f"ids file not found: {path}")
    ids: list[int] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # Strip label prefix like "test ids："
        line = re.sub(r"^[^\d\-]+", "", line).strip()
        if not line:
            continue
        ids.extend(parse_ids_arg(line))
    return sorted(set(ids))


def resolve_topic_ids(
    *,
    ids_text: str = "",
    ids_file: str | Path = "",
    export_all: bool = False,
) -> list[int] | None:
    """Return sorted topic ids, or ``None`` when exporting all assignments."""
    if export_all:
        return None
    ids: list[int] = []
    if str(ids_text or "").strip():
        ids.extend(parse_ids_arg(ids_text))
    if ids_file:
        ids.extend(load_ids_from_file(Path(ids_file)))
    ids = sorted(set(ids))
    if not ids:
        raise ValueError("no topic ids: pass --ids, --ids-file, or --all")
    return ids
