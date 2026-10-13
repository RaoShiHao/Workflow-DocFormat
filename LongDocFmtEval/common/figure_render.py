"""脚本合成图渲染器（尺寸精确可控、中文可显）。

- render_chart：统计图表（bar/line/pie/scatter），从结构化 spec 出图；缺数值则按种子随机填。
- render_diagram：抽象流程/架构图（圆角框 + 箭头），从 {nodes, edges, layout} 出图。
- render_placeholder：兜底占位图（浅框 + 居中标题）。
所有函数按 width_cm×height_cm 出 PNG，供 officecli 内联嵌入时不变形。
"""
from __future__ import annotations

import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

CM = 1 / 2.54
_PALETTE = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860"]


def _setup_font() -> None:
    for fam in ("Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS", "DejaVu Sans"):
        try:
            plt.rcParams["font.sans-serif"] = [fam]
            break
        except Exception:  # noqa: BLE001
            continue
    plt.rcParams["axes.unicode_minus"] = False


def _new_ax(w_cm: float, h_cm: float):
    fig, ax = plt.subplots(figsize=(max(w_cm, 3) * CM, max(h_cm, 3) * CM), dpi=200)
    return fig, ax


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _as_str_labels(x) -> list[str]:
    out: list[str] = []
    for v in x or []:
        if isinstance(v, dict):
            v = v.get("name") or v.get("label") or v.get("x") or next(iter(v.values()), "")
        out.append(str(v))
    return out or ["A", "B", "C", "D"]


def _scalar_num(v) -> float | None:
    if isinstance(v, dict):
        for k in ("value", "v", "n", "y"):
            cand = v.get(k)
            if cand is not None and not isinstance(cand, (list, dict)):
                v = cand
                break
        else:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_num_list(y, n: int, rnd: random.Random) -> list[float]:
    if isinstance(y, dict):
        y = y.get("y") or y.get("values") or y.get("data") or list(y.values())
    if not isinstance(y, list):
        y = []
    nums = [num for v in y if (num := _scalar_num(v)) is not None]
    while len(nums) < n:
        nums.append(round(rnd.uniform(0.2, 1.0), 2))
    return nums[:n] if n else nums


def _series_values(s: dict):
    y = s.get("y")
    if y is None:
        y = s.get("values") or s.get("data")
    if isinstance(y, dict):
        y = y.get("y") or y.get("values") or y.get("data")
    return y


def _normalize_series(spec: dict, x: list, rnd: random.Random) -> list[dict]:
    """Accept both {series:[{name,y}]} and LLM-shaped {y:[{name,y}, ...]}."""
    series = spec.get("series")
    y = spec.get("y")
    if not series and isinstance(y, list) and y and isinstance(y[0], dict):
        series = y
        y = None
    if not series:
        series = [{"name": spec.get("name") or spec.get("ylabel") or "数值", "y": y}]
    n = len(x)
    out: list[dict] = []
    for i, s in enumerate(series):
        if not isinstance(s, dict):
            s = {"y": s}
        out.append({
            "name": str(s.get("name") or s.get("label") or f"S{i}"),
            "y": _as_num_list(_series_values(s), n, rnd),
        })
    return out


def _hide_spines(ax) -> None:
    for side in ("top", "right"):
        try:
            ax.spines[side].set_visible(False)
        except Exception:  # noqa: BLE001
            continue


def render_chart(spec: dict, path: Path, w_cm: float = 10, h_cm: float = 8, seed: int = 0) -> Path:
    """spec: {type: bar|line|pie|scatter, x:[...], y:[...] 或 series:[{name,y}], xlabel, ylabel, title}."""
    _setup_font()
    rnd = random.Random(seed)
    ctype = spec.get("type") or "bar"
    if isinstance(ctype, dict):
        ctype = ctype.get("type") or "bar"
    ctype = str(ctype).lower()
    x = _as_str_labels(spec.get("x") or spec.get("categories"))
    title = spec.get("title") or ""
    xlabel = spec.get("xlabel") or ""
    ylabel = spec.get("ylabel") or ""
    series = _normalize_series(spec, x, rnd)

    fig, ax = _new_ax(w_cm, h_cm)

    if ctype == "pie":
        vals = series[0]["y"]
        ax.pie(vals, labels=x, autopct="%1.0f%%", colors=_PALETTE[: len(x)],
               textprops={"fontsize": 8})
        ax.axis("equal")
    elif ctype == "line":
        for i, s in enumerate(series):
            ax.plot(x, s["y"], marker="o", color=_PALETTE[i % len(_PALETTE)], label=s.get("name", f"S{i}"))
        if len(series) > 1:
            ax.legend(fontsize=8)
        _hide_spines(ax)
    elif ctype == "scatter":
        for i, s in enumerate(series):
            xs = list(range(len(s["y"]))) if not _is_num(x) else x
            ax.scatter(xs, s["y"], color=_PALETTE[i % len(_PALETTE)], label=s.get("name", f"S{i}"), s=24)
        _hide_spines(ax)
    else:  # bar (含分组柱)
        n = len(series)
        width = 0.8 / max(n, 1)
        idx = list(range(len(x)))
        for i, s in enumerate(series):
            offs = [j + (i - (n - 1) / 2) * width for j in idx]
            bars = ax.bar(offs, s["y"], width=width, color=_PALETTE[i % len(_PALETTE)], label=s.get("name", f"S{i}"))
            if n == 1:
                for b, v in zip(bars, s["y"]):
                    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), _num(v),
                            ha="center", va="bottom", fontsize=7)
        ax.set_xticks(idx)
        ax.set_xticklabels(x, fontsize=8)
        if n > 1:
            ax.legend(fontsize=8)
        _hide_spines(ax)

    if title:
        ax.set_title(title, fontsize=10)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9)
    if ylabel and ctype != "pie":
        ax.set_ylabel(ylabel, fontsize=9)
    fig.tight_layout()
    return _save(fig, path)


def render_diagram(spec: dict, path: Path, w_cm: float = 9, h_cm: float = 12) -> Path:
    """spec: {nodes:[str...], edges:[[i,j]...]（缺省=链式）, layout: vertical|horizontal, title}.

    箭头画在框与框之间的空隙里，避免一条线「穿过」所有框、只在末端露出箭头的观感。
    """
    _setup_font()
    nodes = [str(t) for t in (spec.get("nodes") or ["步骤一", "步骤二", "步骤三"])]
    layout = (spec.get("layout") or "vertical").lower()
    n = len(nodes)
    edges = _normalize_chain_edges(n, spec.get("edges"))

    fig, ax = _new_ax(w_cm, h_cm)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    maxchars = max((len(t) for t in nodes), default=4)
    centers: list[tuple[float, float]] = []
    if layout.startswith("h"):
        # 框宽 + 间隙：间隙专留给箭头；框 zorder 高于箭头，即使线段略伸进框内也被盖住
        gap = 1.0 if n > 1 else 0.0
        usable = max(8.2 - gap * (n - 1), 2.0)
        box_w = usable / max(n, 1)
        box_h = 2.6
        fs = _fit_fontsize(w_cm * (box_w / 10), maxchars)
        y0 = 5.0 - box_h / 2
        x_cursor = 0.5
        for i in range(n):
            x0 = x_cursor
            cx, cy = x0 + box_w / 2, 5.0
            centers.append((cx, cy))
            _box(ax, x0, y0, box_w, box_h, nodes[i], fs)
            x_cursor = x0 + box_w + gap
        for a, b in edges:
            x1 = centers[a][0] + box_w / 2 + 0.08
            x2 = centers[b][0] - box_w / 2 - 0.08
            if x2 <= x1 + 0.2:
                mid = (centers[a][0] + centers[b][0]) / 2
                x1, x2 = mid - 0.25, mid + 0.25
            _arrow(ax, (x1, 5.0), (x2, 5.0), zorder=1)
    else:
        gap = 0.85 if n > 1 else 0.0
        usable = max(8.2 - gap * (n - 1), 2.0)
        box_h = usable / max(n, 1)
        box_w = 7.0
        fs = _fit_fontsize(w_cm * 0.7, maxchars)
        y_cursor = 9.5
        for i in range(n):
            y_top = y_cursor
            cy = y_top - box_h / 2
            centers.append((5.0, cy))
            _box(ax, (10 - box_w) / 2, y_top - box_h, box_w, box_h, nodes[i], fs)
            y_cursor = y_top - box_h - gap
        for a, b in edges:
            y1 = centers[a][1] - box_h / 2 - 0.08
            y2 = centers[b][1] + box_h / 2 + 0.08
            if y1 <= y2 + 0.2:
                mid = (centers[a][1] + centers[b][1]) / 2
                y1, y2 = mid + 0.25, mid - 0.25
            _arrow(ax, (5.0, y1), (5.0, y2), zorder=1)

    if spec.get("title"):
        ax.text(5, 9.85, spec["title"], ha="center", va="top", fontsize=10, color="#1A2A44")
    return _save(fig, path)


def _normalize_chain_edges(n: int, edges) -> list[list[int]]:
    """流程默认相邻链式；跨级长箭头会导致「一根线穿过多框」。"""
    chain = [[i, i + 1] for i in range(max(n - 1, 0))]
    if not edges or n < 2:
        return chain
    parsed: list[list[int]] = []
    for e in edges:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        try:
            a, b = int(e[0]), int(e[1])
        except (TypeError, ValueError):
            continue
        if 0 <= a < n and 0 <= b < n and a != b:
            parsed.append([a, b])
    if not parsed:
        return chain
    # 若全是跨级（|a-b|>1）且没有相邻边，改回链式，避免穿框长箭
    if all(abs(a - b) > 1 for a, b in parsed) and not any(abs(a - b) == 1 for a, b in parsed):
        return chain
    return parsed


def render_placeholder(spec: dict, path: Path, w_cm: float = 10, h_cm: float = 8) -> Path:
    _setup_font()
    fig, ax = _new_ax(w_cm, h_cm)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.4, 0.4), 9.2, 9.2, boxstyle="round,pad=0.1",
                                linewidth=1.2, edgecolor="#B0B0B0", facecolor="#F5F5F5", linestyle="--"))
    ax.text(5, 5, spec.get("title") or "图片占位", ha="center", va="center",
            fontsize=11, color="#8A8A8A", wrap=True)
    return _save(fig, path)


def _box(ax, x, y, w, h, text, fontsize: float = 9) -> None:
    # zorder 高于箭头，避免箭杆「穿过」框内文字
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08,rounding_size=0.2",
                                linewidth=1.3, edgecolor="#33507A", facecolor="#EAF1FB", zorder=4))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize,
            color="#1A2A44", wrap=True, zorder=5)


def _fit_fontsize(box_w_cm: float, nchars: int, base: float = 9.0, lo: float = 4.5) -> float:
    """让最长标签横向放进框宽：一个中文字约等于字号(pt) 的宽度。"""
    avail_pt = max(box_w_cm, 0.5) * 28.35 * 0.9  # cm→pt(≈28.35) 留 10% 余量
    return max(lo, min(base, avail_pt / max(nchars, 1)))


def _arrow(ax, p0, p1, zorder: int = 3) -> None:
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle="-|>", mutation_scale=14, linewidth=1.35,
        color="#33507A", shrinkA=0, shrinkB=0, zorder=zorder,
        connectionstyle="arc3,rad=0"))


def _is_num(seq) -> bool:
    return bool(seq) and all(isinstance(v, (int, float)) for v in seq)


def _num(v) -> str:
    if isinstance(v, float) and 0 < v <= 1:
        return f"{v:.0%}"
    return str(v)
