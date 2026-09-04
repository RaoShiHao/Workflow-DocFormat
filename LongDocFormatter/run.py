"""CLI: python -m LongDocFormatter --source ... --output ... [--template ...] [--requirement ...]"""

from __future__ import annotations

import argparse
from pathlib import Path

from LongDocFormatter.constants import DEFAULT_CONFIG_PATH, DEFAULT_LLM_CONFIG, DEFAULT_PROMPT_DIR
from LongDocFormatter.llm.factory import build_models_from_config
from LongDocFormatter.workflow.longdoc_graph import LongDocFormatter, normalize_io


def _requirement_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_file() and path.suffix.lower() in {".txt", ".md", ".json"}:
        return path.read_text(encoding="utf-8")
    return text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Format a Word document. Inputs: template, requirement, source, output. "
            "template and requirement are optional, but at least one is required."
        )
    )
    parser.add_argument(
        "--template",
        default="",
        help="Optional template.docx. Empty for requirement-only (plain text) samples.",
    )
    parser.add_argument(
        "--requirement",
        default="",
        help="Optional requirement text, or a path to .txt/.md. Empty for template-only samples.",
    )
    parser.add_argument("--source", required=True, help="Unformatted source/init .docx")
    parser.add_argument("--output", required=True, help="Output .docx")
    parser.add_argument(
        "--artifacts-dir",
        default="",
        help="Artifacts directory (default: <output parent>/artifacts).",
    )
    parser.add_argument("--llm-config", default=str(DEFAULT_LLM_CONFIG), help="LLM config YAML.")
    parser.add_argument("--prompt-dir", default=str(DEFAULT_PROMPT_DIR), help="Bilingual prompt dir.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="longdoc_config.yaml.")
    parser.add_argument("--locale", default="en", help="Prompt locale: en (default) or zh.")
    parser.add_argument(
        "--model",
        default="",
        help="Registry key in llm_config.yaml models: (default: yaml active).",
    )
    args = parser.parse_args(argv)
    if not str(args.template or "").strip() and not str(args.requirement or "").strip():
        parser.error("at least one of --template or --requirement is required")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    template = str(Path(args.template).resolve()) if args.template.strip() else ""
    requirement = _requirement_text(args.requirement)
    source = str(Path(args.source).resolve())
    output = str(Path(args.output).resolve())
    artifacts = (
        str(Path(args.artifacts_dir).resolve())
        if args.artifacts_dir.strip()
        else str(Path(output).parent / "artifacts")
    )
    state = normalize_io(
        {
            "template": template,
            "requirement": requirement,
            "source": source,
            "output": output,
            "artifacts_dir": artifacts,
            "locale": args.locale,
        }
    )
    language_model, multimodal_model, llm_params = build_models_from_config(args.llm_config, model=args.model or None)
    formatter = LongDocFormatter(
        language_model=language_model,
        multimodal_model=multimodal_model,
        prompt_dir=args.prompt_dir,
        config_path=args.config,
    )
    result = formatter.run(
        state,
        llm_kwargs=llm_params.get("language_model") or {},
        mm_kwargs=llm_params.get("multimodal_model") or {},
    )
    print(f"[OK] formatted: {result.get('output') or output}")
    print(f"[INFO] artifacts: {result.get('artifacts_dir') or artifacts}")
    print(f"[INFO] input_mode: {result.get('input_mode')}")


if __name__ == "__main__":
    main()
