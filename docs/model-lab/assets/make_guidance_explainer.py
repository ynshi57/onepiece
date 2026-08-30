"""Render an explainer figure for the guidance-line flip bug and the 3-layer fix.

Uses the REAL greedy algorithm (mirrors centerline_from_mask) so the drawn lines
are faithful, not hand-waved. numpy-only connected components (no scipy).
"""
import os
import tempfile

os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp())

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch

# --- CJK font (macOS) --------------------------------------------------------
for cand in [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]:
    if os.path.exists(cand):
        try:
            font_manager.fontManager.addfont(cand)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=cand).get_name()
            break
        except Exception:
            pass
plt.rcParams["axes.unicode_minus"] = False

H, W = 12, 16
FREE = np.array([0.80, 0.93, 0.80])   # light green
BUS = np.array([0.20, 0.45, 0.85])    # blue obstacle
SKY = np.array([0.72, 0.78, 0.88])    # light gray sky (not traversable)


def runs(row):
    out, s = [], None
    for i, v in enumerate(row):
        if v and s is None:
            s = i
        elif not v and s is not None:
            out.append((s, i))
            s = None
    if s is not None:
        out.append((s, len(row)))
    return out


def greedy(mask, prev_seed=None):
    """Mirror centerline_from_mask: per-row nearest run to running center."""
    pts = []
    prev = prev_seed
    for r in range(H - 1, -1, -1):
        rr = runs(mask[r])
        if not rr:
            if prev is None:
                continue
            break
        target = (W * 0.5) if prev is None else prev
        best = min(rr, key=lambda a: abs((a[0] + a[1]) / 2.0 - target))
        c = (best[0] + best[1]) / 2.0
        prev = c
        pts.append((c, r))
    return pts


def components(mask):
    """4-connectivity labels via numpy BFS. Returns list of sets of (r,c)."""
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for r in range(H):
        for c in range(W):
            if mask[r, c] and not seen[r, c]:
                stack = [(r, c)]
                seen[r, c] = True
                cells = []
                while stack:
                    y, x = stack.pop()
                    cells.append((y, x))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
                comps.append(set(cells))
    return comps


def component_medial(cells):
    """Per-row midpoint of a connected component's cells (bottom-up)."""
    pts = []
    for r in range(H - 1, -1, -1):
        xs = sorted(x for (y, x) in cells if y == r)
        if not xs:
            continue
        pts.append(((xs[0] + xs[-1]) / 2.0, r))
    return pts


def img_of(mask):
    im = np.tile(FREE, (H, W, 1))
    im[~mask] = BUS           # obstacle (bus / wall) in blue
    im[0, :] = SKY            # top row is sky (not traversable) in gray
    return im


def draw_mask(ax, mask, title):
    ax.imshow(img_of(mask), origin="upper", extent=[0, W, H, 0])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=12, pad=6)
    # grid lines
    for x in range(W + 1):
        ax.axvline(x, color="white", lw=0.3, alpha=0.25)
    for y in range(H + 1):
        ax.axhline(y, color="white", lw=0.3, alpha=0.25)


def plot_line(ax, pts, color, ls="-", lw=3, label=None, marker="o"):
    if not pts:
        return
    xs = [p[0] + 0.0 for p in pts]
    ys = [p[1] + 0.5 for p in pts]
    ax.plot(xs, ys, color=color, ls=ls, lw=lw, marker=marker, ms=5, label=label,
            solid_capstyle="round")


def seed_arrow(ax, x, color="#ff9f0a", text="从画面正中 0.5 播种"):
    ax.add_patch(FancyArrowPatch((W / 2, H + 0.9), (x, H - 0.4),
                                 arrowstyle="-|>", mutation_scale=14,
                                 color=color, lw=2))
    ax.annotate(text, (W / 2, H + 1.05), color=color, fontsize=9,
                ha="center", va="bottom")


# --- Two frames: near-identical scene, tiny change --------------------------
# Frame A: bus splits bottom into left/right corridors; left is the big real
# sidewalk. Top row is sky (blocked) so left/right are SEPARATE components.
def base_mask():
    # Left corridor (cols 0-6) is the WIDE real sidewalk; a bus+wall band
    # (cols 7-11) blocks the middle; right corridor (cols 12-15) is a narrow
    # sliver. Sky (row 0) keeps left & right as SEPARATE components.
    m = np.ones((H, W), dtype=bool)
    m[0:3, :] = False        # sky/buildings above the ground: not traversable
    m[3:H, 7:12] = False     # bus + wall band splits the ground into L/R
    return m


maskA = base_mask()
# Frame B: same scene, but a small central gap opens at the very bottom
# (a pedestrian steps aside / model noise) -> a short central "nub" that joins
# the narrow right sliver.
maskB = base_mask()
maskB[H - 2:H, 8:13] = True

lineA = greedy(maskA)
lineB = greedy(maskB)

# Fix (1): largest bottom-anchored connected component -> its medial line.
def largest_bottom_component(mask):
    comps = components(mask)
    bottom = [c for c in comps if any(y >= H - 2 for (y, x) in c)]
    return max(bottom, key=len) if bottom else max(comps, key=len)


medA = component_medial(largest_bottom_component(maskA))
medB = component_medial(largest_bottom_component(maskB))

# Fix (2): temporal lock -> seed frame B from frame A's bottom center.
seedB = lineA[0][0] if lineA else W / 2
lineB_locked = greedy(maskB, prev_seed=seedB)

# Fix (3): bimodal -> both corridors as candidates on frame B.
comps_b = [c for c in components(maskB) if any(y >= H - 2 for (y, x) in c)]
comps_b.sort(key=len, reverse=True)
prim = component_medial(comps_b[0]) if comps_b else []
seco = component_medial(comps_b[1]) if len(comps_b) > 1 else []

# ---------------------------------------------------------------------------
fig = plt.figure(figsize=(15, 9.2))
gs = fig.add_gridspec(2, 3, hspace=0.32, wspace=0.14, height_ratios=[1, 1])

fig.suptitle("引导线为什么会左右翻面 · 以及三层修法", fontsize=17, fontweight="bold", y=0.98)

# Row 0 — the problem
axA = fig.add_subplot(gs[0, 0])
draw_mask(axA, maskA, "帧①  （绿=可走，蓝=障碍/公交车，灰=天空）")
seed_arrow(axA, lineA[0][0] if lineA else W / 2)
plot_line(axA, lineA, "#8e2fd6", label="贪心中心线 → 走左")
axA.legend(loc="lower right", fontsize=8, framealpha=0.85)

axB = fig.add_subplot(gs[0, 1])
draw_mask(axB, maskB, "帧②  公交车没动，只是底部开了个小缝")
seed_arrow(axB, lineB[0][0] if lineB else W / 2)
plot_line(axB, lineB, "#8e2fd6", label="贪心中心线 → 翻到右")
axB.legend(loc="lower right", fontsize=8, framealpha=0.85)

axT = fig.add_subplot(gs[0, 2])
axT.axis("off")
axT.text(0.0, 1.0,
         "现在的算法（真值和预测共用）：\n\n"
         "① 从画面正中 0.5 播种\n"
         "    第一行取『中心最接近 0.5』的那段可走区\n\n"
         "② 每行取离中心最近的 run\n"
         "    run = 一行里连续的可走像素段\n\n"
         "③ 贪心向上，零跨帧记忆\n"
         "    每帧都重新从 0.5 开始，不看上一帧\n\n"
         "→ 左右两块几乎对称时，播种就是『抛硬币』：\n"
         "   一个小缝 / 一点噪声，整条线就翻面。\n"
         "   全数据集 5–6% 相邻帧对是这种翻面。",
         va="top", ha="left", fontsize=11, color="#e0402b",
         family=plt.rcParams["font.family"])

# Row 1 — the fix
ax1 = fig.add_subplot(gs[1, 0])
draw_mask(ax1, maskB, "① 单帧内：连通域中轴")
plot_line(ax1, medB, "#0a84ff", label="最大连通可走块的中轴")
ax1.legend(loc="lower right", fontsize=8, framealpha=0.85)
ax1.text(0.5, -0.09, "取『面积最大的连通可走块』的中轴，\n不再被中间小缝拽走 → 帧①帧②都走左", transform=ax1.transAxes,
         ha="center", va="top", fontsize=9.5, color="#0a84ff")

ax2 = fig.add_subplot(gs[1, 1])
draw_mask(ax2, maskB, "② 跨帧：时间锁")
plot_line(ax2, lineB_locked, "#30d158", label="用上一帧播种 → 保持左")
ax2.add_patch(FancyArrowPatch((seedB, H - 0.4), (seedB, H + 0.9),
                              arrowstyle="-|>", mutation_scale=14, color="#30d158", lw=2))
ax2.annotate("种子来自上一帧的路径", (seedB, H + 1.05), color="#30d158",
             fontsize=9, ha="center", va="bottom")
ax2.legend(loc="lower right", fontsize=8, framealpha=0.85)
ax2.text(0.5, -0.09, "连续视频里用上一帧路径播种 + 侧别切换滞回，\n不给『抛硬币』的机会（对真机体验最关键）",
         transform=ax2.transAxes, ha="center", va="top", fontsize=9.5, color="#1a8f3c")

ax3 = fig.add_subplot(gs[1, 2])
draw_mask(ax3, maskB, "③ 诚实处理双峰")
plot_line(ax3, prim, "#8e2fd6", ls="-", label="主线（面积大/连续）")
plot_line(ax3, seco, "#ffd60a", ls="--", lw=2, label="候选线（降置信）")
ax3.legend(loc="lower right", fontsize=8, framealpha=0.85)
ax3.text(0.5, -0.09, "左右真的都能走时，不假装唯一：\n给主线 + 候选线，并降低置信度",
         transform=ax3.transAxes, ha="center", va="top", fontsize=9.5, color="#8e2fd6")

out = os.path.join(os.path.dirname(__file__), "guidance-line-flip-explainer.png")
fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
print("wrote", out)
