"""Step 5 runner: specimen + template.docx from step4 styles.

Input  = ``C*/step4_styles/``（``role_params.json`` / ``template.json``；
         未拆分时仍可读 ``step4_template/``）。
Output = ``C*/step5_template/``：specimen_plan.json / template.docx / assets_preview …

Usage:
  python LongDocFmtEval/pipeline_auto/scripts/run_step5.py --id 1 --theme 1
  python LongDocFmtEval/pipeline_auto/scripts/run_step5.py --id 3 --rerender --respecimen
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.common.draft_resolve import parse_ids_arg  # noqa: E402
from LongDocFmtEval.pipeline_auto.auto_paths import (  # noqa: E402
    TEMPLATES_DIR,
    TOPICS_DIR,
    drop_redundant_copies,
    resolve_work_file,
)
from LongDocFmtEval.pipeline_auto.topic_layout import (  # noqa: E402
    resolve_topic_dir,
    styles_dir as topic_styles_dir,
    template_dir as topic_template_dir,
)


def _write_seed_ir(out_dir: Path) -> None:
    """写出与 seed 同构的 map/styles/outline 等，供人工改后 sync / 重渲。"""
    try:
        from LongDocFmtEval.pipeline_auto.scripts.step3_seed_ir import (  # noqa: PLC0415
            write_step3_seed_ir,
        )
        summary = write_step3_seed_ir(out_dir)
        print(
            f"[seed-ir] objects={summary.get('objects')} roles={summary.get('roles')} "
            f"tables={summary.get('tables')} → {out_dir}"
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] seed IR 写出失败（不影响 T*）：{e}")


def _render_preview(out_dir: Path) -> None:
    try:
        from LongDocFmtEval.pipeline_auto.scripts.render_template_docx import (  # noqa: PLC0415
            render,
        )
        path = render(out_dir, out_name="template.docx", assets_name="assets_preview.json")
        print(f"[docx] {path}")
        leftover = Path(out_dir) / "preview.docx"
        if leftover.is_file() and leftover.resolve() != path.resolve():
            leftover.unlink()
            print("    [cleanup] 已去掉冗余 preview.docx（与 template.docx 同文件）")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 预览渲染失败（不影响 T* 产物）：{e}")
        print(
            f"       可手动重试：python LongDocFmtEval/pipeline_auto/scripts/"
            f"render_template_docx.py --template-dir \"{out_dir}\""
        )


def _build_figures(out_dir: Path, *, overwrite: bool, no_render: bool = False) -> bool:
    """内联调用图构建（preview 变体）。``no_render=True`` 只出资产，不写 template.docx。"""
    try:
        from LongDocFmtEval.pipeline_auto.scripts import build_figures  # noqa: PLC0415
        return build_figures.build_one(
            out_dir, variant="preview", overwrite=overwrite,
            temperature=0.5, max_tokens=20480, seed=1234, no_render=no_render,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 图构建失败（不影响 T* 产物）：{e}")
        return False


def _template_docx_ready(out_dir: Path) -> bool:
    """True when a real template.docx exists (not an empty create_docx shell)."""
    docx = Path(out_dir) / "template.docx"
    if not docx.is_file():
        return False
    return docx.stat().st_size > 10_000


def template_docx_ready(out_dir: Path) -> bool:
    """Public alias for overlapped / cache checks."""
    return _template_docx_ready(out_dir)


def _write_source_meta(out_dir: Path, styles_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "step": 5,
        "styles_dir": str(styles_dir.resolve()).replace("\\", "/"),
        "template_dir": str(out_dir.resolve()).replace("\\", "/"),
    }
    (out_dir / "source_step5.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def _cleanup_preview_leftover(out_dir: Path) -> None:
    leftover = out_dir / "preview.docx"
    template = out_dir / "template.docx"
    if not template.is_file() and leftover.is_file():
        leftover.replace(template)
    elif leftover.is_file() and template.is_file() and leftover.resolve() != template.resolve():
        leftover.unlink()


def prepare_specimen_assets(
    out_dir: Path,
    figures: bool,
    *,
    styles_dir: Path | None = None,
    figures_overwrite: bool = False,
    overwrite_specimen: bool = False,
    chapter_workers: int = 1,
    max_tokens: int = 10480,
) -> bool:
    """Step5 LLM 段：specimen + seed IR +（可选）preview 图资产；不渲 template.docx。"""
    from LongDocFmtEval.pipeline_auto.scripts.run_step4 import (  # noqa: PLC0415
        _normalize_existing_tstar,
    )
    from LongDocFmtEval.pipeline_auto.seed.template_specimen import (  # noqa: PLC0415
        DEFAULT_SPECIMEN_MAX_TOKENS,
        ensure_specimen_plan,
    )

    styles = Path(styles_dir or out_dir).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_source_meta(out_dir, styles)
    _normalize_existing_tstar(styles)
    tok = int(max_tokens) if max_tokens is not None else DEFAULT_SPECIMEN_MAX_TOKENS
    ensure_specimen_plan(
        out_dir,
        overwrite=overwrite_specimen,
        chapter_workers=chapter_workers,
        max_tokens=tok,
    )
    _write_seed_ir(out_dir)
    dropped = drop_redundant_copies(out_dir)
    if dropped:
        print(f"[dedupe] 去掉与 step3/4 重复的 {', '.join(dropped)}")
    if figures:
        _build_figures(out_dir, overwrite=figures_overwrite, no_render=True)
    return (out_dir / "specimen_plan.json").is_file()


def render_template_docx_only(
    out_dir: Path,
    figures: bool,
    *,
    styles_dir: Path | None = None,
    figures_overwrite: bool = False,
) -> bool:
    """Step5 officecli 段：在 specimen/资产就绪后渲 template.docx。"""
    styles = Path(styles_dir or out_dir).resolve()
    out_dir = Path(out_dir).resolve()
    _write_source_meta(out_dir, styles)
    if figures and _build_figures(out_dir, overwrite=figures_overwrite, no_render=False):
        _cleanup_preview_leftover(out_dir)
        return _template_docx_ready(out_dir) or (out_dir / "template.docx").is_file()
    _render_preview(out_dir)
    _cleanup_preview_leftover(out_dir)
    return _template_docx_ready(out_dir) or (out_dir / "template.docx").is_file()


def finish_preview(
    out_dir: Path,
    figures: bool,
    *,
    styles_dir: Path | None = None,
    figures_overwrite: bool = False,
    overwrite_specimen: bool = False,
    chapter_workers: int = 1,
    max_tokens: int = 10480,
) -> None:
    """出 specimen + seed IR + template.docx（串行一体；overlapped 请拆 prepare/render）。

    figures_overwrite=True：重规划并重绘 assets_preview。
    overwrite_specimen=True：重跑按 Sec* 组织短标本的 LLM。
    """
    prepare_specimen_assets(
        out_dir, figures,
        styles_dir=styles_dir,
        figures_overwrite=figures_overwrite,
        overwrite_specimen=overwrite_specimen,
        chapter_workers=chapter_workers,
        max_tokens=max_tokens,
    )
    render_template_docx_only(
        out_dir, figures,
        styles_dir=styles_dir,
        figures_overwrite=False,
    )


def run_one(
    styles_dir: Path,
    *,
    out_dir: Path | None = None,
    overwrite: bool = False,
    figures: bool = True,
    rerender: bool = False,
    respecimen: bool = False,
    chapter_workers: int = 1,
    max_tokens: int = 10480,
) -> bool:
    styles_dir = Path(styles_dir).resolve()
    out_dir = Path(out_dir).resolve() if out_dir else styles_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rp_json = resolve_work_file(styles_dir, "role_params.json")
    if not rp_json.is_file():
        rp_json = resolve_work_file(out_dir, "role_params.json")
    tpl_json = resolve_work_file(styles_dir, "template.json")
    if not tpl_json.is_file():
        tpl_json = resolve_work_file(out_dir, "template.json")
    if not rp_json.is_file() and not tpl_json.is_file():
        print(f"[skip] 缺 role_params.json（请先跑 step4）：{styles_dir}")
        return False

    _write_source_meta(out_dir, styles_dir)

    if rerender:
        print(f"[rerender] 保留 T*，重出 template.docx：{out_dir}")
        finish_preview(
            out_dir, figures,
            styles_dir=styles_dir,
            figures_overwrite=False,
            overwrite_specimen=respecimen,
            chapter_workers=chapter_workers,
            max_tokens=max_tokens,
        )
        return True

    if not overwrite and _template_docx_ready(out_dir) and not respecimen:
        print(f"[cache] step5 已有 template.docx，跳过：{out_dir}")
        print("         （改渲染请加 --rerender；重跑标本请加 --respecimen）")
        return True

    print(f"\n=== step5 template.docx: {out_dir} ===")
    finish_preview(
        out_dir, figures,
        styles_dir=styles_dir,
        figures_overwrite=bool(overwrite),
        overwrite_specimen=bool(overwrite or respecimen),
        chapter_workers=chapter_workers,
        max_tokens=max_tokens,
    )
    return _template_docx_ready(out_dir) or (out_dir / "template.docx").is_file()


def _jobs_for_topic_ids(
    genre_ids: list[int],
    theme_ids: list[int],
) -> list[tuple[Path, Path, str]]:
    """Resolve (styles_dir, template_out, label)."""
    from LongDocFmtEval.pipeline_auto.auto_paths import DATASETS_DIR  # noqa: PLC0415

    def _label(path: Path) -> str:
        for root in (DATASETS_DIR, TOPICS_DIR):
            try:
                return str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
        return path.name

    jobs: list[tuple[Path, Path, str]] = []
    for gid in genre_ids:
        try:
            topic = resolve_topic_dir(gid)
        except FileNotFoundError:
            topic = None
        if topic is None or not topic.is_dir():
            for tid in theme_ids:
                d = TEMPLATES_DIR / str(gid)
                jobs.append((d, d, f"id={gid} theme={tid} flat"))
            continue
        for tid in theme_ids:
            sd = topic_styles_dir(topic, tid)
            td = topic_template_dir(topic, tid)
            if not (
                resolve_work_file(sd, "role_params.json").is_file()
                or resolve_work_file(sd, "template.json").is_file()
            ):
                continue
            jobs.append((sd, td, f"id={gid} theme={tid} → {_label(td)}"))
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Step5：按 Sec* 组织短标本并渲 template.docx。推荐 --id + --theme。"
    )
    ap.add_argument("--id", dest="genre_id", type=int, default=None, help="体裁 id")
    ap.add_argument("--ids", default="", help="多个体裁 id，如 1,26")
    ap.add_argument("--theme", type=int, default=None, help="主题 id（默认 1）")
    ap.add_argument("--themes", default="", help="多个主题 id，如 1,2 或 1-3")
    ap.add_argument("--styles-dir", default="", help="显式 step4_styles 目录")
    ap.add_argument("--out-dir", default="", help="step5 输出目录（默认 C*/step5_template）")
    ap.add_argument(
        "--overwrite", action="store_true",
        help="重跑标本 LLM 并重绘 preview 图后嵌入 template.docx",
    )
    ap.add_argument(
        "--rerender", action="store_true",
        help="不重填 T*：保留 role_params，重出 specimen（若缺）并重渲 template.docx",
    )
    ap.add_argument(
        "--respecimen", action="store_true",
        help="重跑按 Sec* 组织短标本的 LLM（保留 role_params）",
    )
    ap.add_argument("--no-figures", action="store_true", help="不出图，template.docx 用占位框")
    ap.add_argument(
        "--chapter-workers", type=int, default=None,
        help="specimen Sec* 组 LLM 并发（默认见 pipeline DEFAULT_CHAPTER_WORKERS=2）",
    )
    ap.add_argument(
        "--max-tokens", type=int, default=10480,
        help="specimen 每 Sec* 组 LLM 基础预算（按 must 数抬升，重试最高 20480）",
    )
    args = ap.parse_args()

    from LongDocFmtEval.pipeline_auto.auto_paths import DEFAULT_CHAPTER_WORKERS  # noqa: PLC0415
    chapter_workers = max(1, int(
        args.chapter_workers if args.chapter_workers is not None else DEFAULT_CHAPTER_WORKERS
    ))
    max_tokens = max(1024, int(args.max_tokens))

    if args.overwrite and args.rerender:
        print("[warn] 同时指定了 --overwrite 与 --rerender：本次只按 --rerender")

    if args.styles_dir:
        sd = Path(args.styles_dir)
        if not sd.is_dir():
            ap.error(f"--styles-dir 不存在: {sd}")
        out = Path(args.out_dir) if args.out_dir else sd
        run_one(
            sd, out_dir=out, overwrite=args.overwrite,
            figures=not args.no_figures, rerender=args.rerender,
            respecimen=bool(args.respecimen),
            chapter_workers=chapter_workers,
            max_tokens=max_tokens,
        )
        return

    theme_ids = parse_ids_arg(args.themes) if args.themes else (
        [] if args.theme is None else [args.theme]
    )
    genre_ids = parse_ids_arg(args.ids) if args.ids else (
        [] if args.genre_id is None else [args.genre_id]
    )
    if genre_ids and not theme_ids:
        theme_ids = [1]
    if not genre_ids:
        ap.error("请指定 --id <体裁id> [--theme <主题id>]，或 --styles-dir")

    try:
        jobs = _jobs_for_topic_ids(genre_ids, theme_ids)
    except FileNotFoundError as e:
        ap.error(str(e))
    if not jobs:
        ap.error("找不到 step4 T*（请先跑 step4，或检查 --ids / --theme）。")
    print(f"[batch] {len(jobs)} 个样例参与")
    ok = 0
    for idx, (sd, td, label) in enumerate(jobs, 1):
        print(f"\n----- ({idx}/{len(jobs)}) {label} -----")
        out = Path(args.out_dir) if (args.out_dir and len(jobs) == 1) else td
        if run_one(
            sd, out_dir=out, overwrite=args.overwrite,
            figures=not args.no_figures, rerender=args.rerender,
            respecimen=bool(args.respecimen),
            chapter_workers=chapter_workers,
            max_tokens=max_tokens,
        ):
            ok += 1
    print(f"\n[done] {ok}/{len(jobs)} 写出 template.docx")


# if __name__ == "__main__":
#     main()


if __name__ == "__main__":
    import sys
    indexes = [
1,2,3,6,7,8,10,11,15,16,
18,19,20,21,23,27,29,30,31,33,
35,37,38,39,44,45,46,47,49,50,
51,52,54,55,56,57,58,59,60,61,
62,63,65,67,68,70,
]
    for i in indexes:
        sys.argv = [
            "run_step5.py",
            "--id", str(i),
            "--themes", "1,2,3",
            "--rerender",
        ]
        main()