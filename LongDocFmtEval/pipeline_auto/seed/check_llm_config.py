"""Print / validate pipeline_auto seed/main LLM config (base_url, model, api_key).

Usage:
  python LongDocFmtEval/pipeline_auto/seed/check_llm_config.py
  python LongDocFmtEval/pipeline_auto/seed/check_llm_config.py --ping
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.llm import load_yaml
from LongDocFmtEval.pipeline_auto.auto_llm import llm_config_for, stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import LLM_CONFIG_PATH, SEED_LLM_CONFIG_PATH


def main() -> None:
    ap = argparse.ArgumentParser(description="Check pipeline_auto llm / seed_llm config yaml")
    ap.add_argument("--llm-config", default="", help="override yaml path")
    ap.add_argument(
        "--route",
        default="seed",
        choices=("role", "seed", "role_library"),
        help="which auto_paths yaml to check (default: seed)",
    )
    ap.add_argument("--ping", action="store_true", help="send a tiny chat request")
    args = ap.parse_args()

    path = Path(args.llm_config) if args.llm_config else llm_config_for(args.route)
    print(f"config: {path}")
    print(f"exists: {path.exists()}")
    if not path.exists():
        print(
            f"missing — create/edit {SEED_LLM_CONFIG_PATH} or {LLM_CONFIG_PATH}",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = load_yaml(path)
    lm = cfg.get("language_model") or {}
    provider = str(lm.get("provider") or "openai")
    block = cfg.get(provider) or {}
    key = str(block.get("api_key") or "").strip()
    print(f"provider: {provider}")
    print(f"model: {lm.get('model')}")
    print(f"base_url: {block.get('base_url')}")
    print(f"api_key: {'(set, len=' + str(len(key)) + ')' if key else '(EMPTY — fill yaml)'}")

    if not key:
        sys.exit(2)

    if args.ping:
        text = stream_llm_text(
            system="Reply with exactly: pong",
            user="ping",
            route=args.route,
            llm_config=path,
            max_tokens=16,
            temperature=0,
            progress=False,
        )
        print(f"ping_reply: {text!r}")


if __name__ == "__main__":
    main()
