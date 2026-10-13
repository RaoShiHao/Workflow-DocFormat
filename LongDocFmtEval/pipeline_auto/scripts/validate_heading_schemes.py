"""Validate heading scheme templates and domain pools before assignment.

Example:
  python LongDocFmtEval/pipeline_auto/scripts/validate_heading_schemes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.auto_paths import CONFIG_DIR
from LongDocFmtEval.pipeline_auto.seed.heading_numbering import (
    format_prefix,
    load_schemes,
    to_chinese_numeral,
    to_roman,
    validate_schemes_automation,
)
from LongDocFmtEval.pipeline_auto.seed.heading_numbering_spec import render_numbering_spec_md
from LongDocFmtEval.pipeline_auto.seed.training_factors import (
    load_pools,
    validate_domain_pools,
)


def _demo_table() -> str:
    schemes = load_schemes()
    lines = ["", "=== automation demo (sample prefixes) ==="]
    demos = [
        ("zh_chapter_cn_decimal", 1, [1], "L1"),
        ("zh_chapter_cn_decimal", 2, [1, 2], "L2 under ch1"),
        ("zh_chapter_cn_decimal", 2, [2, 1], "L2 reset under ch2"),
        ("zh_chapter_cn_decimal", 3, [1, 1, 2], "L3"),
        ("zh_gov_legal", 1, [2], "L1"),
        ("zh_gov_legal", 2, [2, 1], "L2 reset under 二、"),
        ("zh_chinese_hierarchy", 2, [2, 1], "L2 reset under ch2"),
        ("en_chapter_section", 2, [2, 1], "Section reset"),
        ("en_chapter_section", 3, [1, 2, 1], "Subsection reset"),
        ("en_outline_mixed", 2, [2, 1], "B. under II."),
        ("en_chapter_alpha", 2, [2, 1], "A. under ch2"),
        ("iso_decimal", 3, [1, 2, 3], "ISO L3"),
    ]
    for sid, level, counters, note in demos:
        pref = format_prefix(schemes[sid], level, counters).rstrip()
        lines.append(f"  {sid:28} {note:22} {counters} → {pref!r}")
    lines.append("")
    lines.append(f"  CN(3)={to_chinese_numeral(3)!r}  ROMAN(4)={to_roman(4)!r}")
    return "\n".join(lines)


def main() -> None:
    pools_path = CONFIG_DIR / "training_factor_pools.yaml"
    pools = load_pools(pools_path)
    schemes = load_schemes()

    pool_errors = validate_domain_pools(pools, known_schemes=set(schemes))
    scheme_errors = validate_schemes_automation(schemes)

    print(f"schemes: {len(schemes)}")
    print(f"domain pools: {len(pools.get('domain_pools') or {})}")
    print(_demo_table())

    sample_md = render_numbering_spec_md(
        "zh_chapter_cn_decimal", locale="zh", document_numbered=True, schemes=schemes,
    )
    print("\n=== sample numbering_spec (zh_chapter_cn_decimal, excerpt) ===")
    for line in sample_md.splitlines()[:14]:
        print(line)

    if pool_errors:
        print("\nPOOL ERRORS:")
        for e in pool_errors:
            print(f"  - {e}")
    if scheme_errors:
        print("\nSCHEME ERRORS:")
        for e in scheme_errors:
            print(f"  - {e}")

    if pool_errors or scheme_errors:
        raise SystemExit(1)
    print("\n[ok] all domain pools and scheme templates pass automation checks")


if __name__ == "__main__":
    main()
