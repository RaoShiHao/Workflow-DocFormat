"""Set per-sample table render overrides (step5 template + step6 document).

Examples::

  # 该样例封面信息表不要用规则硬填（template + document 均生效）
  python LongDocFmtEval/pipeline_auto/scripts/set_render_overrides.py \\
      --id 106 --complexity C1 --skip-cover-info-tables \\
      --note "封面信息表规则填格异常"

  python LongDocFmtEval/pipeline_auto/scripts/run_step5.py --id 106 --rerender
  python LongDocFmtEval/pipeline_auto/scripts/run_step6.py --id 106 --complexity C1 --rerender

  python LongDocFmtEval/pipeline_auto/scripts/set_render_overrides.py \\
      --id 106 --complexity C1 --clear
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from LongDocFmtEval.pipeline_auto.seed.render_overrides import (  # noqa: E402
    RENDER_OVERRIDES_NAME,
    delete_render_overrides,
    load_render_overrides,
    save_render_overrides,
)
from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: E402
    find_topic_dir_by_id,
    template_dir,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="设置/清除 step5+step6 共用的 render_overrides.json",
    )
    ap.add_argument("--id", dest="genre_id", type=int, required=True)
    ap.add_argument("--complexity", default="C1", help="C1 / C2 / C3")
    ap.add_argument(
        "--skip-cover-info-tables", action="store_true",
        help="不渲染封面/项目信息表（TblCover*）；step5 与 step6 均生效",
    )
    ap.add_argument(
        "--no-skip-cover-info-tables", action="store_true",
        help="清除 skip_cover_info_tables 标记（保留 note 等其它字段）",
    )
    ap.add_argument("--note", default="", help="备注（写入 override 文件）")
    ap.add_argument("--clear", action="store_true", help="删除 render_overrides.json")
    ap.add_argument("--show", action="store_true", help="打印当前 override")
    args = ap.parse_args()

    topic = find_topic_dir_by_id(args.genre_id)
    td = template_dir(topic, args.complexity)
    if not td.is_dir():
        ap.error(f"step5 目录不存在: {td}")

    if args.show:
        data = load_render_overrides(td)
        path = td / RENDER_OVERRIDES_NAME
        if not data:
            print(f"[show] 无 override（{path} 不存在）")
            return
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if args.clear:
        if delete_render_overrides(td):
            print(f"[clear] 已删除 {td / RENDER_OVERRIDES_NAME}")
        else:
            print(f"[clear] 无 {RENDER_OVERRIDES_NAME}")
        return

    data = load_render_overrides(td)
    if args.skip_cover_info_tables:
        data["skip_cover_info_tables"] = True
    if args.no_skip_cover_info_tables:
        data.pop("skip_cover_info_tables", None)
    if args.note.strip():
        data["note"] = args.note.strip()

    path = save_render_overrides(td, data)
    print(f"[write] {path}")
    print(
        f"       step5: python LongDocFmtEval/pipeline_auto/scripts/run_step5.py "
        f"--id {args.genre_id} --complexity {args.complexity} --rerender"
    )
    print(
        f"       step6: python LongDocFmtEval/pipeline_auto/scripts/run_step6.py "
        f"--id {args.genre_id} --complexity {args.complexity} --rerender"
    )
    if data.get("skip_cover_info_tables"):
        print("       → 跳过封面/项目信息表（TblCover* / 封面键值表）")
    print(
        "       （默认）step6 对任意缺 content.table 的表块也不做规则硬填"
    )


if __name__ == "__main__":
    main()
