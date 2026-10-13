"""Resolve experiment / dataset / eval paths from agent + skill + model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Storage / CLI agent ids match LongDocFmt/{agent}/.
# deepseek-harness is the SDK product name; it only aliases to dp-harness.
AGENT_ALIASES = {
    "dp-harness": "dp-harness",
    "deepseek-harness": "dp-harness",
    "codex": "codex",
}

SKILL_ALIASES = {
    "officecli": "officecli",
    "officecli-docx": "officecli",
    "officecli-orch": "officecli-orch",
    "officecli_orch": "officecli-orch",
    "officecli-orchestration": "officecli-orch",
    "docxcli": "docxcli",
    "docx-cli": "docxcli",
    "docx": "docxcli",
    "longformatter": "longformatter",
    "longdoc-format": "longformatter",
    "longdoc-formatter": "longformatter",
    "docfmt-toolskill": "longformatter",
    "docfmt-skill": "officecli-orch",
    # No-skills control arms (code/OOXML only; same path segment as runners).
    "noskills": "noskills",
    "no-skills": "noskills",
    "no_skills": "noskills",
}


def canon_agent(name: str) -> str:
    key = (name or "").strip().lower()
    if key not in AGENT_ALIASES:
        raise SystemExit(f"unknown agent {name!r}; expected one of {sorted(AGENT_ALIASES)}")
    return AGENT_ALIASES[key]


def canon_skill(name: str) -> str:
    key = (name or "").strip().lower()
    if key not in SKILL_ALIASES:
        raise SystemExit(f"unknown skill {name!r}; expected one of {sorted(SKILL_ALIASES)}")
    return SKILL_ALIASES[key]


def result_dir(*, experiment_root: Path, agent: str, skill: str, model: str, sample_id: str) -> Path:
    return Path(experiment_root) / canon_agent(agent) / canon_skill(skill) / model / str(sample_id)


def eval_dir(*, eval_root: Path, agent: str, skill: str, model: str, sample_id: str) -> Path:
    """Deprecated unused ExperimentEval path. Prefer ``sample_eval_dir``."""
    return Path(eval_root) / canon_agent(agent) / canon_skill(skill) / model / str(sample_id)


def sample_eval_dir(run_dir: Path) -> Path:
    """``{sample}/eval/`` next to that sample's ``output.docx``."""
    return Path(run_dir) / "eval"


def resolve_output_docx(run_dir: Path) -> Path | None:
    """Locate the produced .docx. Prefer ``output.docx``, then ``run.json``, then a single sibling .docx."""
    root = Path(run_dir)
    preferred = root / "output.docx"
    if preferred.is_file():
        return preferred
    run_json = root / "run.json"
    if run_json.is_file():
        try:
            data = load_json(run_json)
        except Exception:
            data = None
        paths = data.get("paths") if isinstance(data, dict) else None
        if isinstance(paths, dict):
            for key in ("output_docx", "workspace_output"):
                raw = paths.get(key)
                if not raw:
                    continue
                candidate = Path(str(raw))
                if not candidate.is_file() and candidate.name:
                    candidate = root / candidate.name
                if candidate.is_file() and candidate.suffix.lower() == ".docx":
                    return candidate
    siblings = sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() == ".docx"
    )
    if len(siblings) == 1:
        return siblings[0]
    return None


def sample_dir(dataset_root: Path, sample_id: str) -> Path:
    return Path(dataset_root) / str(sample_id)


def result_model_dir(*, experiment_root: Path, agent: str, skill: str, model: str) -> Path:
    return result_dir(
        experiment_root=experiment_root,
        agent=agent,
        skill=skill,
        model=model,
        sample_id="_",
    ).parent


def discover_result_sample_ids(model_dir: Path) -> list[str]:
    """Sample folder names under ``{agent}/{skill}/{model}/`` (skip ``_*``)."""
    if not model_dir.is_dir():
        return []
    ids: list[str] = []
    for child in model_dir.iterdir():
        if child.is_dir() and not child.name.startswith("_"):
            ids.append(child.name)
    return sorted(ids, key=lambda name: (not name.isdigit(), int(name) if name.isdigit() else name))


def load_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
