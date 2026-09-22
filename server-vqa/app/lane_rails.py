"""Occupied-lane rails as polylines (UFLD row-anchor family), not pixel blobs.

Paint rails: one x per image row (UFLDv2 decode when weights exist, otherwise
the same row-anchor teacher on CamVid Lane pixels). Curb rails: one x per row
at pavement|sidewalk or pavement|void. Occupied pair = the two polylines the
vehicle heading sits between. Guidance = G2 midpoint, hard-clamped inside.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.road_structure import _cluster_columns, fit_guidance_g2, heading_curvature_stats

# Lane polylines are smooth. This is a geometric limit, not a pixel fudge:
# a real lane edge does not corner by more than this in a local heading window.
LANE_MAX_TURN_DEG = 40.0

_UFLD_NAMES = (
    "VQASeeLaneUFLDv2.mlpackage",
    "VQASeeLaneUFLDv2.mlmodelc",
    "VQASeeLaneUFLDv2_6bit.mlpackage",
    "ufldv2_culane_res18.pth",
    "culane_res18.pth",
    "ufldv2_culane_res18_320x1600.onnx",
    "ufld_v2_culane_res18_320x1600.onnx",
    "ufldv2.onnx",
)

_UFLD_RUNTIME: dict[str, object] = {}
_UFLD_LAST_ERROR: str | None = None
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_UFLD_MIN_BYTES = {
    ".pth": 80_000_000,
    ".pt": 80_000_000,
    ".onnx": 8_000_000,
}


@dataclass
class OccupiedRails:
    left: np.ndarray
    right: np.ndarray
    mids: np.ndarray
    points: list[tuple[float, float]] = field(default_factory=list)
    paint_source: str = "gt_row_anchor"
    left_kind: str = "paint"
    right_kind: str = "curb"
    stats: dict[str, float] = field(default_factory=dict)
    paint_rails: list[np.ndarray] = field(default_factory=list)

    @classmethod
    def empty(cls, height: int, *, paint_source: str = "gt_row_anchor") -> OccupiedRails:
        nan = np.full(height, np.nan, dtype=np.float64)
        return cls(left=nan.copy(), right=nan.copy(), mids=nan.copy(), paint_source=paint_source)


def default_ufld_model_path() -> Path | None:
    env = os.environ.get("VQASEE_MODELS_DIR")
    roots: list[Path] = []
    if env:
        roots.append(Path(env))
    roots.append(Path.home() / ".cache" / "vqasee" / "models")
    for root in roots:
        for name in _UFLD_NAMES:
            path = root / name
            if path.exists() and _ufld_weight_ready(path):
                return path
    return None


def _ufld_weight_ready(path: Path) -> bool:
    if path.is_dir():
        return True
    if not path.is_file():
        return False
    minimum = _UFLD_MIN_BYTES.get(path.suffix.lower(), 1)
    try:
        return path.stat().st_size >= minimum
    except OSError:
        return False


def default_ufld_repo() -> Path | None:
    env = os.environ.get("VQASEE_UFLD_REPO")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".cache" / "vqasee" / "ext" / "Ultra-Fast-Lane-Detection-v2")
    for path in candidates:
        if (path / "model" / "model_culane.py").is_file():
            return path
    return None


def _import_convert_ufld():
    root = Path(__file__).resolve().parents[2] / "deploy" / "ios" / "convert_ufldv2_lane_coreml.py"
    spec = importlib.util.spec_from_file_location("convert_ufldv2_lane_coreml", root)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_numpy_pred(pred: dict) -> dict:
    out = {}
    for key in ("loc_row", "loc_col", "exist_row", "exist_col"):
        val = pred[key]
        if hasattr(val, "detach"):
            val = val.detach().cpu().numpy()
        arr = np.asarray(val)
        if arr.ndim == 3:
            arr = arr[None, ...]
        out[key] = arr
    return out


def _pil_to_nchw01(pil) -> np.ndarray:
    arr = np.asarray(pil.convert("RGB"), dtype=np.float32) / 255.0
    return np.transpose(arr, (2, 0, 1))[None, ...]


def _imagenet_norm(nchw01: np.ndarray) -> np.ndarray:
    mean = np.array(_IMAGENET_MEAN, dtype=np.float32).reshape(1, 3, 1, 1)
    std = np.array(_IMAGENET_STD, dtype=np.float32).reshape(1, 3, 1, 1)
    return (nchw01 - mean) / std


def _infer_torch(rgb: np.ndarray, path: Path, decode_mod) -> dict | None:
    import torch

    convert_mod = _import_convert_ufld()
    repo = default_ufld_repo()
    if convert_mod is None or repo is None:
        return None
    cache = _UFLD_RUNTIME
    if cache.get("path") != str(path) or cache.get("kind") != "torch":
        cfg = convert_mod.CONFIGS["culane_res18"]
        net = convert_mod.build_net(repo, cfg)
        convert_mod.load_checkpoint(net, path)
        net.eval()
        cache.clear()
        cache.update(path=str(path), kind="torch", net=net, cfg_name="culane")
    net = cache["net"]
    cfg = decode_mod.DECODE["culane"]
    from PIL import Image

    pil = decode_mod.preprocess(Image.fromarray(np.asarray(rgb)).convert("RGB"), cfg)
    x = torch.from_numpy(_imagenet_norm(_pil_to_nchw01(pil)))
    with torch.no_grad():
        out = net(x)
    if isinstance(out, dict):
        return _as_numpy_pred(out)
    loc_row, loc_col, exist_row, exist_col = out
    return _as_numpy_pred(
        {"loc_row": loc_row, "loc_col": loc_col, "exist_row": exist_row, "exist_col": exist_col}
    )


def _infer_onnx(rgb: np.ndarray, path: Path, decode_mod) -> dict | None:
    import onnxruntime as ort

    cache = _UFLD_RUNTIME
    if cache.get("path") != str(path) or cache.get("kind") != "onnx":
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        cache.clear()
        cache.update(path=str(path), kind="onnx", sess=sess, input_name=inp.name, input_shape=tuple(inp.shape))
    sess = cache["sess"]
    shape = cache["input_shape"]
    # Typical UFLDv2: (1, 3, H, W). H=320 W=1600 CULane; W=800 TuSimple.
    net_h = 320
    net_w = 1600
    if len(shape) == 4:
        h_dim, w_dim = shape[2], shape[3]
        if isinstance(h_dim, int) and h_dim > 0:
            net_h = int(h_dim)
        if isinstance(w_dim, int) and w_dim > 0:
            net_w = int(w_dim)
    cfg = decode_mod.DECODE["tusimple"] if net_w <= 800 else decode_mod.DECODE["culane"]
    cfg = dict(cfg)
    cfg["net_h"] = net_h
    cfg["net_w"] = net_w
    from PIL import Image

    pil = decode_mod.preprocess(Image.fromarray(np.asarray(rgb)).convert("RGB"), cfg)
    nchw = _imagenet_norm(_pil_to_nchw01(pil))
    pred_list = sess.run(None, {cache["input_name"]: nchw.astype(np.float32)})
    names = [o.name for o in sess.get_outputs()]
    pred = {name: arr for name, arr in zip(names, pred_list)}
    if "loc_row" not in pred and len(pred_list) >= 4:
        pred = {
            "loc_row": pred_list[0],
            "loc_col": pred_list[1],
            "exist_row": pred_list[2],
            "exist_col": pred_list[3],
        }
    return _as_numpy_pred(pred)


def _infer_coreml(rgb: np.ndarray, path: Path, decode_mod) -> dict | None:
    import coremltools as ct
    from PIL import Image

    cache = _UFLD_RUNTIME
    if cache.get("path") != str(path) or cache.get("kind") != "coreml":
        model = ct.models.MLModel(str(path))
        cache.clear()
        cache.update(path=str(path), kind="coreml", model=model)
    cfg = decode_mod.DECODE["culane"]
    pil = decode_mod.preprocess(Image.fromarray(np.asarray(rgb)).convert("RGB"), cfg)
    pred = cache["model"].predict({"image": pil})
    return _as_numpy_pred(pred)


def _import_ufld_decode():
    root = Path(__file__).resolve().parents[2] / "deploy" / "ios" / "decode_ufldv2_lanes.py"
    spec = importlib.util.spec_from_file_location("decode_ufldv2_lanes", root)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _split_xy_points(
    points: list[tuple[float, float]],
    *,
    max_turn_deg: float = LANE_MAX_TURN_DEG,
) -> list[list[tuple[float, float]]]:
    """Cut a decoded polyline at heading corners before resampling."""
    pts = sorted(((float(x), float(y)) for x, y in points), key=lambda p: p[1])
    if len(pts) < 3:
        return [pts] if pts else []
    groups: list[list[tuple[float, float]]] = []
    cur = [pts[0], pts[1]]
    for x, y in pts[2:]:
        if len(cur) < 2:
            cur.append((x, y))
            continue
        v1 = (cur[-1][0] - cur[-2][0], cur[-1][1] - cur[-2][1])
        v2 = (x - cur[-1][0], y - cur[-1][1])
        if _turn_deg(v1, v2) > max_turn_deg:
            groups.append(cur)
            cur = [(x, y)]
            continue
        cur.append((x, y))
    groups.append(cur)
    return [g for g in groups if len(g) >= 2]


def _rail_is_chord(xs: np.ndarray) -> bool:
    """A lane rail is longer in y than in x. A weld across the road is not."""
    finite = np.flatnonzero(np.isfinite(xs))
    if finite.size < 8:
        return True
    yspan = float(finite.max() - finite.min())
    xspan = float(np.nanmax(xs) - np.nanmin(xs))
    return xspan > 2.0 * max(1.0, yspan)


def polyline_to_row_xs(
    points: list[tuple[float, float]],
    height: int,
    width: int,
) -> np.ndarray:
    """Resample a (x, y) polyline onto one x per image row, then densify."""
    xs = np.full(int(height), np.nan, dtype=np.float64)
    if not points:
        return xs
    pts = sorted(((float(x), float(y)) for x, y in points), key=lambda p: p[1])
    if len(pts) == 1:
        yi = int(round(pts[0][1]))
        if 0 <= yi < height:
            xs[yi] = float(np.clip(pts[0][0], 0.0, width - 1))
        return densify_rail(xs, width=width)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y1 == y0:
            yi = int(np.clip(round(y0), 0, height - 1))
            xs[yi] = float(np.clip(0.5 * (x0 + x1), 0.0, width - 1))
            continue
        y_lo = int(np.ceil(min(y0, y1)))
        y_hi = int(np.floor(max(y0, y1)))
        for y in range(max(0, y_lo), min(height, y_hi + 1)):
            t = (float(y) - y0) / (y1 - y0)
            xs[y] = float(np.clip(x0 + t * (x1 - x0), 0.0, width - 1))
    return densify_rail(xs, width=width)


def try_ufld_paint_rails(
    rgb: np.ndarray | None,
    *,
    model_path: Path | None = None,
) -> list[np.ndarray] | None:
    """Decode UFLDv2 polylines when weights exist. Never raises to the caller."""
    global _UFLD_LAST_ERROR
    _UFLD_LAST_ERROR = None
    if rgb is None:
        return None
    path = model_path if model_path is not None else default_ufld_model_path()
    if path is None or not Path(path).exists():
        return None
    try:
        decode_mod = _import_ufld_decode()
        if decode_mod is None:
            return None
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in {".pth", ".pt"}:
            pred = _infer_torch(rgb, path, decode_mod)
        elif suffix == ".onnx":
            pred = _infer_onnx(rgb, path, decode_mod)
        else:
            pred = _infer_coreml(rgb, path, decode_mod)
        if not pred:
            _UFLD_LAST_ERROR = "empty-pred"
            return None
        cfg = decode_mod.cfg_from_pred(pred)
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
        lanes = decode_mod.decode(pred, width, height, cfg, exist_min=8)
        rails: list[np.ndarray] = []
        for pts in lanes:
            if not pts:
                continue
            groups = _split_xy_points(pts)
            if not groups:
                continue
            frag = max(groups, key=len)
            rail = polyline_to_row_xs(frag, height, width)
            rail = drop_identity_jumps(rail)
            pieces = split_sharp_turns(rail, width=width)
            piece = max(pieces, key=lambda xs: int(np.isfinite(xs).sum()), default=None) if pieces else None
            if piece is None or int(np.isfinite(piece).sum()) < 8:
                continue
            if _rail_is_chord(piece):
                continue
            rails.append(piece)
        if not rails:
            _UFLD_LAST_ERROR = "decoded-empty"
            return None
        return rails
    except Exception as exc:
        _UFLD_LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None


def densify_rail(
    xs: np.ndarray,
    *,
    width: int | None = None,
    hood_rows: int = 12,
    degree: int = 1,
    max_abs_slope: float = 2.5,
    extra_rows: int | None = None,
) -> np.ndarray:
    """Interpolate observed row anchors; short linear extra toward the hood.

    Global high-order fits are banned: they throw a left-curving dash to x=0.
    ``degree`` is accepted for callers but only linear extra is used.
    """
    del degree
    xs = np.asarray(xs, dtype=np.float64).copy()
    height = int(xs.size)
    finite = np.flatnonzero(np.isfinite(xs))
    if finite.size == 0:
        return xs
    out = np.full(height, np.nan, dtype=np.float64)
    if finite.size == 1:
        y0 = int(finite[0])
        out[y0] = float(xs[y0])
        return out
    y0 = int(finite.min())
    y1 = int(finite.max())
    out[y0 : y1 + 1] = np.interp(np.arange(y0, y1 + 1, dtype=np.float64), finite.astype(np.float64), xs[finite])
    extra = int(0.28 * height) if extra_rows is None else int(extra_rows)
    y_hi = min(height - 1 - max(0, int(hood_rows)), y1 + max(0, extra))
    if y_hi > y1:
        tail = finite[-min(12, finite.size) :]
        slope = float(np.polyfit(tail.astype(np.float64), xs[tail], 1)[0])
        slope = float(np.clip(slope, -max_abs_slope, max_abs_slope))
        last_x = float(xs[y1])
        for y in range(y1 + 1, y_hi + 1):
            out[y] = last_x + slope * (y - y1)
    if width is not None:
        finite_out = np.isfinite(out)
        out[finite_out] = np.clip(out[finite_out], 0.0, float(width - 1))
    return out


def _turn_deg(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    a = np.array(v1, dtype=np.float64)
    b = np.array(v2, dtype=np.float64)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    cos = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))


def split_sharp_turns(
    xs: np.ndarray,
    *,
    max_turn_deg: float = LANE_MAX_TURN_DEG,
    width: int | None = None,
) -> list[np.ndarray]:
    """Split a row-anchor rail at heading corners. Uses angles, not pixel gates."""
    xs = np.asarray(xs, dtype=np.float64)
    height = int(xs.size)
    finite = np.flatnonzero(np.isfinite(xs))
    if finite.size < 8:
        return [xs] if finite.size else []
    win = max(4, height // 90)
    cuts: list[int] = [0]
    for i in range(win, finite.size - win):
        y0 = int(finite[i - win])
        y1 = int(finite[i])
        y2 = int(finite[i + win])
        v1 = (float(xs[y1] - xs[y0]), float(y1 - y0))
        v2 = (float(xs[y2] - xs[y1]), float(y2 - y1))
        if _turn_deg(v1, v2) > max_turn_deg:
            if cuts[-1] != i:
                cuts.append(i)
    cuts.append(int(finite.size))
    min_len = max(8, height // 16)
    fragments: list[np.ndarray] = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < min_len:
            continue
        frag = np.full(height, np.nan, dtype=np.float64)
        for k in range(a, b):
            yi = int(finite[k])
            frag[yi] = float(xs[yi])
        fragments.append(densify_rail(frag, width=width, extra_rows=0))
    return fragments or [xs]


def rails_from_lane_mask(
    lane: np.ndarray,
    *,
    gap: int = 6,
    associate_dx: float = 48.0,
    associate_dy: int = 120,
    min_rows: int = 8,
    max_rails: int = 4,
    hood_rows: int = 12,
) -> list[np.ndarray]:
    """Row-anchor teacher: per-row cluster centers, associated near-to-far.

    Parallel rails stay separate (no leftover merge). Dashed gaps stay one
    polyline because each row independently votes an x, then the polynomial
    fills missing rows.
    """
    lane = np.asarray(lane, dtype=bool)
    height, width = lane.shape
    rails: list[dict[int, float]] = []
    last: list[tuple[int, float]] = []
    for y in range(height - 1, -1, -1):
        clusters = _cluster_columns(np.flatnonzero(lane[y]), gap=gap)
        used = [False] * len(clusters)
        for ri, (ly, lx) in enumerate(last):
            if ly - y <= 0 or ly - y > associate_dy:
                continue
            best_i = -1
            best_d = float(associate_dx)
            for i, (lo, hi) in enumerate(clusters):
                if used[i]:
                    continue
                mid = 0.5 * (lo + hi)
                dist = abs(mid - lx)
                if dist < best_d:
                    best_d = dist
                    best_i = i
            if best_i >= 0:
                used[best_i] = True
                mid = 0.5 * (clusters[best_i][0] + clusters[best_i][1])
                rails[ri][y] = mid
                last[ri] = (y, mid)
        for i, (lo, hi) in enumerate(clusters):
            if used[i]:
                continue
            mid = 0.5 * (lo + hi)
            rails.append({y: mid})
            last.append((y, mid))

    scored: list[tuple[int, np.ndarray]] = []
    for sparse in rails:
        if len(sparse) < min_rows:
            continue
        xs = np.full(height, np.nan, dtype=np.float64)
        for y, x in sparse.items():
            xs[int(y)] = float(x)
        scored.append(
            (len(sparse), densify_rail(xs, width=width, hood_rows=hood_rows, extra_rows=0))
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    smooth: list[np.ndarray] = []
    for _n, rail in scored[: max(max_rails, 6)]:
        smooth.extend(split_sharp_turns(rail, width=width))
    smooth.sort(key=lambda rail: int(np.isfinite(rail).sum()), reverse=True)
    return smooth[:max_rails]


def curb_rails(
    pavement: np.ndarray,
    sidewalk: np.ndarray | None = None,
    *,
    hood_rows: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    """One left and one right curb polyline from the main pavement run.

    Image-border pavement (hood labelled as road to x=0/W) is not a curb.
    Sidewalk snap stays inside that main run so island slivers cannot steal it.
    """
    pavement = np.asarray(pavement, dtype=bool)
    height, width = pavement.shape
    left = np.full(height, np.nan, dtype=np.float64)
    right = np.full(height, np.nan, dtype=np.float64)
    sidewalk_b = None if sidewalk is None else np.asarray(sidewalk, dtype=bool)
    border = max(6, int(round(0.04 * width)))
    for y in range(height):
        clusters = [
            span
            for span in _cluster_columns(np.flatnonzero(pavement[y]), gap=8)
            if span[1] - span[0] >= 12
        ]
        if not clusters:
            continue
        lo, hi = max(clusters, key=lambda span: span[1] - span[0])
        if sidewalk_b is not None:
            sw = sidewalk_b[y]
            row = pavement[y]
            right_hits = [
                x for x in range(lo, hi + 1) if x + 1 < width and row[x] and sw[x + 1]
            ]
            left_hits = [
                x for x in range(lo, hi + 1) if x - 1 >= 0 and row[x] and sw[x - 1]
            ]
            if right_hits:
                hi = int(right_hits[-1])
            if left_hits:
                lo = int(left_hits[0])
        if lo > border:
            left[y] = float(lo)
        if hi < width - 1 - border:
            right[y] = float(hi)
    return (
        densify_rail(left, width=width, hood_rows=hood_rows, extra_rows=0),
        densify_rail(right, width=width, hood_rows=hood_rows, extra_rows=0),
    )


def select_occupied_pair(
    rails: list[tuple[np.ndarray, str]],
    *,
    ego_col: int,
    width: int,
    hood_rows: int = 12,
    min_span_ratio: float = 0.06,
    max_span_ratio: float = 0.62,
    min_interior_ratio: float = 0.12,
) -> tuple[np.ndarray, np.ndarray, str, str] | None:
    """Pick the adjacent rail pair whose interval contains heading and looks like one lane."""
    if len(rails) < 2:
        return None
    height = int(rails[0][0].size)
    ego_col = int(np.clip(ego_col, 0, width - 1))
    min_span = max(8.0, min_span_ratio * width)
    max_span = max(min_span + 4.0, max_span_ratio * width)
    def _valid(x_l: float, x_r: float) -> bool:
        span = x_r - x_l
        if span < min_span or span > max_span:
            return False
        interior = min(ego_col - x_l, x_r - ego_col)
        return interior >= max(4.0, min_interior_ratio * span)

    def _hits(y: int) -> list[tuple[float, int, np.ndarray, str]]:
        pts: list[tuple[float, int, np.ndarray, str]] = []
        for idx, (xs, kind) in enumerate(rails):
            x = float(xs[y])
            if np.isfinite(x):
                pts.append((x, idx, xs, kind))
        pts.sort(key=lambda item: item[0])
        return pts

    votes: dict[tuple[int, int], int] = {}
    examples: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, str, str]] = {}
    band_lo = max(0, int(round(0.48 * height)))
    band_hi = height - 1 - max(0, int(hood_rows))
    for y in range(band_hi, band_lo - 1, -1):
        pts = _hits(y)
        for i in range(len(pts) - 1):
            x_l, i_l, r_l, k_l = pts[i]
            x_r, i_r, r_r, k_r = pts[i + 1]
            if x_l <= ego_col <= x_r and _valid(x_l, x_r):
                key = (i_l, i_r)
                votes[key] = votes.get(key, 0) + 1
                examples[key] = (r_l, r_r, k_l, k_r)

    def _pick(keys: list[tuple[int, int]], *, min_share: float = 0.0) -> tuple[np.ndarray, np.ndarray, str, str] | None:
        if not keys or not votes:
            return None
        key = max(keys, key=lambda item: votes[item])
        floor = 6 if height >= 40 else 1
        if votes[key] < floor:
            return None
        if min_share > 0 and votes[key] < min_share * max(votes.values()):
            return None
        return examples[key]

    paint_paint = [
        key
        for key in votes
        if examples[key][2] == "paint" and examples[key][3] == "paint"
    ]
    paint_keys = [
        key for key in votes if examples[key][2] == "paint" or examples[key][3] == "paint"
    ]
    picked = (
        _pick(paint_paint)
        or _pick(paint_keys, min_share=0.35)
        or _pick(list(votes))
    )
    if picked is not None:
        return picked

    start_y = height - 1 - max(0, int(hood_rows))
    start_y = max(0, min(height - 1, start_y))
    for y in range(start_y, band_lo - 1, -1):
        pts = _hits(y)
        for i in range(len(pts) - 1):
            x_l, _i_l, r_l, k_l = pts[i]
            x_r, _i_r, r_r, k_r = pts[i + 1]
            if x_l <= ego_col <= x_r and _valid(x_l, x_r):
                return r_l, r_r, k_l, k_r
    return None


def _paint_mids(lane_row: np.ndarray) -> list[float]:
    return [0.5 * (lo + hi) for lo, hi in _cluster_columns(np.flatnonzero(lane_row), gap=6)]


def _edge_candidates(
    pav_row: np.ndarray,
    sw_row: np.ndarray | None,
    width: int,
    side: str,
) -> list[float]:
    cands: list[float] = []
    clusters = [
        span
        for span in _cluster_columns(np.flatnonzero(pav_row), gap=8)
        if span[1] - span[0] >= 8
    ]
    for lo, hi in clusters:
        if side == "right":
            cands.append(float(hi))
            if sw_row is not None:
                for x in range(hi, lo - 1, -1):
                    if x + 1 < width and pav_row[x] and sw_row[x + 1]:
                        cands.append(float(x))
                        break
        else:
            cands.append(float(lo))
            if sw_row is not None:
                for x in range(lo, hi + 1):
                    if x - 1 >= 0 and pav_row[x] and sw_row[x - 1]:
                        cands.append(float(x))
                        break
    return cands


def _pick_near(cands: list[float], pred: float, max_dx: float) -> float | None:
    if not cands or not np.isfinite(pred):
        return None
    x = min(cands, key=lambda cand: abs(cand - pred))
    if abs(x - pred) > max_dx:
        return None
    return float(x)


def _local_slope(ys: list[int], xs: list[float], *, max_abs: float = 2.5) -> float:
    if len(ys) < 2:
        return 0.0
    tail = min(8, len(ys))
    slope = float(np.polyfit(np.asarray(ys[-tail:], dtype=np.float64), np.asarray(xs[-tail:], dtype=np.float64), 1)[0])
    return float(np.clip(slope, -max_abs, max_abs))


def track_occupied_pair(
    left: np.ndarray,
    right: np.ndarray,
    *,
    pavement: np.ndarray,
    sidewalk: np.ndarray | None = None,
    lane: np.ndarray | None = None,
    left_kind: str = "paint",
    right_kind: str = "curb",
    ego_col: int,
    hood_rows: int = 12,
    max_dx: float = 80.0,
    obstacle: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Keep the occupied pair's identity: follow local slope, drop island jumps.

    Far-field interpolation onto a leftover pavement blob (x→0) is discarded.
    Near-field right edge is the candidate nearest the predicted x, including
    image-border curbs — do not chord inward.
    Heading column selects the lock row only. Do not freeze a rail on
    ``ego_col ± 8``: on a left bend the right curb crosses image centre and
    that freeze is a vertical line up a lamp post.
    """
    left_in = np.asarray(left, dtype=np.float64)
    right_in = np.asarray(right, dtype=np.float64)
    pavement = np.asarray(pavement, dtype=bool)
    height, width = pavement.shape
    max_dx = max(float(max_dx), 0.18 * width)
    ego_col = int(np.clip(ego_col, 0, width - 1))
    out_l = np.full(height, np.nan, dtype=np.float64)
    out_r = np.full(height, np.nan, dtype=np.float64)
    hood = max(0, int(hood_rows))
    band_lo = max(0, int(round(0.50 * height)))
    band_hi = height - 1 - hood
    target = int(round(0.68 * height))
    valid: list[int] = []
    for y in range(band_lo, band_hi + 1):
        if (
            np.isfinite(left_in[y])
            and np.isfinite(right_in[y])
            and float(left_in[y]) + 8.0 < ego_col < float(right_in[y]) - 8.0
        ):
            valid.append(y)
    if not valid:
        return left_in, right_in
    preferred = [y for y in valid if 0.55 * height <= y <= 0.80 * height]
    pool = preferred or valid
    lock_y = min(pool, key=lambda y: abs(y - target))
    out_l[lock_y] = float(left_in[lock_y])
    out_r[lock_y] = float(right_in[lock_y])

    def _seed(xs: np.ndarray, y0: int) -> tuple[list[int], list[float]]:
        ys: list[int] = []
        vs: list[float] = []
        for y in range(max(0, y0 - 24), y0 + 1):
            if np.isfinite(xs[y]):
                ys.append(y)
                vs.append(float(xs[y]))
        if not ys:
            ys = [y0]
            vs = [float(xs[y0])]
        return ys, vs

    obs_row = None if obstacle is None else np.asarray(obstacle, dtype=bool)

    def _clear(y: int, x: float, *, margin: int = 0) -> bool:
        if not np.isfinite(x):
            return False
        xi = int(np.clip(round(float(x)), 0, width - 1))
        if obs_row is None:
            return True
        lo = max(0, xi - int(margin))
        hi = min(width, xi + int(margin) + 1)
        return not bool(obs_row[y, lo:hi].any())

    def _hit(
        kind: str,
        side: str,
        y: int,
        pred: float,
        last: float | None = None,
    ) -> float | None:
        sw = None if sidewalk is None else sidewalk[y]
        if kind == "paint" and lane is not None:
            paint_tol = min(max_dx, max(36.0, 0.05 * width))
            mids = [x for x in _paint_mids(lane[y]) if _clear(y, x, margin=0)]
            picked = _pick_near(mids, pred, paint_tol)
            # A leftover blob next to a car must not steal the dash identity.
            if (
                picked is not None
                and last is not None
                and abs(picked - float(last)) > paint_tol
            ):
                picked = None
            if picked is not None:
                return picked
            if not bool(pavement[y].any()) or not _clear(y, pred, margin=0):
                return None
            return float(np.clip(pred, 0.0, width - 1))
        cands = [x for x in _edge_candidates(pavement[y], sw, width, side) if _clear(y, x)]
        picked = _pick_near(cands, pred, max_dx)
        if picked is not None:
            return picked
        if not _clear(y, pred):
            return None
        xi = int(np.clip(round(pred), 0, width - 1))
        if bool(pavement[y, xi]):
            return float(np.clip(pred, 0.0, width - 1))
        return None

    def _walk(y_from: int, y_to: int, step: int) -> None:
        hist_l_y, hist_l_x = _seed(left_in, y_from)
        hist_r_y, hist_r_x = _seed(right_in, y_from)
        y = y_from + step
        while (step > 0 and y <= y_to) or (step < 0 and y >= y_to):
            pred_l = hist_l_x[-1] + _local_slope(hist_l_y, hist_l_x) * step
            pred_r = hist_r_x[-1] + _local_slope(hist_r_y, hist_r_x) * step
            hit_l = _hit(left_kind, "left", y, pred_l, last=hist_l_x[-1])
            hit_r = _hit(right_kind, "right", y, pred_r, last=hist_r_x[-1])
            if hit_l is None or hit_r is None or hit_r - hit_l < 8.0:
                break
            out_l[y] = hit_l
            out_r[y] = hit_r
            hist_l_y.append(y)
            hist_l_x.append(hit_l)
            hist_r_y.append(y)
            hist_r_x.append(hit_r)
            y += step

    _walk(lock_y, height - 1 - max(0, int(hood_rows)), 1)
    _walk(lock_y, 0, -1)

    def _extend_paint_far(out: np.ndarray, kind: str) -> None:
        if kind != "paint" or lane is None:
            return
        finite = np.flatnonzero(np.isfinite(out))
        if finite.size < 2:
            return
        hist_y = [int(v) for v in finite[:8]]
        hist_x = [float(out[v]) for v in hist_y]
        y = int(finite.min()) - 1
        floor = max(0, int(round(0.32 * height)))
        skipped = 0
        far_tol = min(max_dx, max(72.0, 0.08 * width))
        while y >= floor:
            pred = hist_x[0] + _local_slope(hist_y, hist_x) * (y - hist_y[0])
            mids = [x for x in _paint_mids(lane[y]) if _clear(y, x, margin=0)]
            hit = _pick_near(mids, pred, far_tol)
            if hit is not None and abs(hit - hist_x[0]) > far_tol:
                hit = None
            if hit is None:
                if not bool(pavement[y].any()) and not mids:
                    break
                skipped += 1
                if skipped > 40:
                    break
                y -= 1
                continue
            skipped = 0
            out[y] = hit
            hist_y.insert(0, y)
            hist_x.insert(0, hit)
            hist_y = hist_y[:8]
            hist_x = hist_x[:8]
            y -= 1

    _extend_paint_far(out_l, left_kind)
    _extend_paint_far(out_r, right_kind)
    out_l = drop_identity_jumps(out_l)
    out_r = drop_identity_jumps(out_r)
    if left_kind == "curb":
        out_l = clip_obstacle_far(out_l, obstacle)
    if right_kind == "curb":
        out_r = clip_obstacle_far(out_r, obstacle)
    in_n = min(int(np.isfinite(left_in).sum()), int(np.isfinite(right_in).sum()))
    out_n = min(int(np.isfinite(out_l).sum()), int(np.isfinite(out_r).sum()))
    if out_n < 8 or (in_n >= 16 and out_n < 0.35 * in_n):
        return drop_identity_jumps(left_in), drop_identity_jumps(right_in)
    return out_l, out_r


def drop_identity_jumps(xs: np.ndarray, *, max_dx: float | None = None, max_turn_deg: float = LANE_MAX_TURN_DEG) -> np.ndarray:
    """Drop the far fragment at the first sharp heading corner.

    ``max_dx`` is ignored: a horizontal chord is a ~90° turn, caught by angle.
    """
    del max_dx
    out = np.asarray(xs, dtype=np.float64).copy()
    finite = np.flatnonzero(np.isfinite(out))
    if finite.size < 3:
        return out
    for i in range(1, finite.size - 1):
        y0, y1, y2 = int(finite[i - 1]), int(finite[i]), int(finite[i + 1])
        v1 = (float(out[y1] - out[y0]), float(y1 - y0))
        v2 = (float(out[y2] - out[y1]), float(y2 - y1))
        if _turn_deg(v1, v2) > max_turn_deg:
            out[:y2] = np.nan
            break
    return out


def trim_border_glued(xs: np.ndarray, width: int, *, border: int = 3) -> np.ndarray:
    """Drop row anchors glued to the image border (false hood extra, not a rail)."""
    out = np.asarray(xs, dtype=np.float64).copy()
    width = int(width)
    border = max(1, int(border))
    finite = np.isfinite(out)
    glued = finite & ((out <= border) | (out >= float(width - 1 - border)))
    out[glued] = np.nan
    return out


def clip_obstacle_far(xs: np.ndarray, obstacle: np.ndarray | None) -> np.ndarray:
    """From the hood toward the horizon, drop the rail at the first obstacle pixel.

    Stops a curb that has already walked onto a lamp post from being stroked as
    a vertical yellow line. Near-side points stay.
    """
    out = np.asarray(xs, dtype=np.float64).copy()
    if obstacle is None:
        return out
    obs = np.asarray(obstacle, dtype=bool)
    _height, width = obs.shape
    del _height
    finite = np.flatnonzero(np.isfinite(out))
    if finite.size == 0:
        return out
    for y in finite[::-1]:
        xi = int(np.clip(round(float(out[y])), 0, width - 1))
        if bool(obs[y, xi]):
            out[: int(y) + 1] = np.nan
            break
    return out


def guidance_between_rails(
    left: np.ndarray,
    right: np.ndarray,
    *,
    pavement: np.ndarray | None = None,
    obstacle: np.ndarray | None = None,
    lam: float = 80000.0,
    margin: float = 1.0,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """G2 midpoint of the occupied pair, hard-clamped to [left, right]."""
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    height = int(left.size)
    mids = np.full(height, np.nan, dtype=np.float64)
    lo = np.minimum(left, right)
    hi = np.maximum(left, right)
    both = np.isfinite(lo) & np.isfinite(hi) & (hi > lo)
    mids[both] = 0.5 * (lo[both] + hi[both])
    fitted = fit_guidance_g2(mids, lo, hi, lam=lam, margin=margin)
    for y in range(height):
        if not np.isfinite(fitted[y]) or not both[y]:
            continue
        inner_lo = float(lo[y]) + float(margin)
        inner_hi = float(hi[y]) - float(margin)
        if inner_hi > inner_lo:
            fitted[y] = float(np.clip(fitted[y], inner_lo, inner_hi))
        else:
            fitted[y] = 0.5 * (float(lo[y]) + float(hi[y]))
    width = int(pavement.shape[1]) if pavement is not None else int(max(1, np.nanmax(hi) + 1 if np.isfinite(hi).any() else 1))
    points: list[tuple[float, float]] = []
    started = False
    for y in range(height - 1, -1, -1):
        if not np.isfinite(fitted[y]):
            if started:
                break
            continue
        ui = int(np.clip(round(float(fitted[y])), 0, width - 1))
        if obstacle is not None and bool(obstacle[y, ui]):
            if started:
                break
            continue
        if pavement is not None and not bool(pavement[y, ui]):
            if started:
                break
            continue
        started = True
        points.append((float(fitted[y]), float(y)))
    return fitted, points


def rail_to_points(xs: np.ndarray) -> list[tuple[float, float]]:
    """Finite (x, y) samples of a densified rail, near-to-far (bottom first)."""
    xs = np.asarray(xs, dtype=np.float64)
    points: list[tuple[float, float]] = []
    for y in range(xs.size - 1, -1, -1):
        if np.isfinite(xs[y]):
            points.append((float(xs[y]), float(y)))
    return points


def occupied_lane_rails(
    lane: np.ndarray,
    pavement: np.ndarray,
    *,
    sidewalk: np.ndarray | None = None,
    ego_col: int,
    obstacle: np.ndarray | None = None,
    rgb: np.ndarray | None = None,
    ufld_model: Path | str | None | bool = "auto",
    mask_source: str = "gt_row_anchor",
    lam: float = 80000.0,
) -> OccupiedRails:
    """Model-first occupied rails: UFLD polylines if present, else mask row anchors."""
    lane = np.asarray(lane, dtype=bool)
    pavement = np.asarray(pavement, dtype=bool)
    height, width = pavement.shape
    hood_rows = max(8, int(round(0.02 * height)))
    paint_source = str(mask_source or "gt_row_anchor")
    paint: list[np.ndarray] | None = None
    ufld_error: str | None = None
    model_path: Path | None
    if str(mask_source) == "twinlite" or ufld_model is False or ufld_model is None:
        model_path = None
        want_ufld = False
    elif ufld_model == "auto":
        model_path = None
        want_ufld = True
    else:
        model_path = Path(ufld_model)
        want_ufld = True
    if want_ufld:
        paint = try_ufld_paint_rails(rgb, model_path=model_path)
        if paint:
            paint_source = "ufld"
        else:
            ufld_error = _UFLD_LAST_ERROR
    teacher = rails_from_lane_mask(lane, hood_rows=hood_rows)
    if paint:
        if len(paint) < 2:
            merged = list(paint)
            for rail in teacher:
                if _rail_is_chord(rail):
                    continue
                med = float(np.nanmedian(rail)) if np.isfinite(rail).any() else None
                if med is None:
                    continue
                if any(
                    np.isfinite(u).any() and abs(float(np.nanmedian(u)) - med) < 0.06 * width
                    for u in paint
                ):
                    continue
                merged.append(rail)
                paint_source = "ufld+gt"
            paint = merged
    else:
        paint = teacher
        paint_source = str(mask_source or "gt_row_anchor")

    def _border_rail(xs: np.ndarray) -> bool:
        if not np.isfinite(xs).any():
            return True
        med = float(np.nanmedian(xs))
        return med <= 8.0 or med >= width - 9

    min_paint_rows = max(8, height // 16)
    quality_paint: list[np.ndarray] = []
    for rail in paint:
        trimmed = trim_border_glued(rail, width)
        if _border_rail(trimmed):
            continue
        if int(np.isfinite(trimmed).sum()) < min_paint_rows:
            continue
        quality_paint.append(trimmed)

    left_curb, right_curb = curb_rails(pavement, sidewalk, hood_rows=hood_rows)
    candidates: list[tuple[np.ndarray, str]] = [(r, "paint") for r in quality_paint]
    if not _border_rail(left_curb):
        candidates.append((left_curb, "curb"))
    if not _border_rail(right_curb):
        candidates.append((right_curb, "curb"))
    pair = select_occupied_pair(
        candidates, ego_col=int(ego_col), width=width, hood_rows=hood_rows
    )
    if pair is None:
        empty = OccupiedRails.empty(height, paint_source=paint_source)
        empty.paint_rails = quality_paint
        empty.stats = heading_curvature_stats(empty.mids)
        if ufld_error:
            empty.stats["ufld_error"] = ufld_error
        return empty
    left, right, left_kind, right_kind = pair
    left, right = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        sidewalk=sidewalk,
        lane=lane,
        left_kind=left_kind,
        right_kind=right_kind,
        ego_col=int(ego_col),
        hood_rows=hood_rows,
        obstacle=obstacle,
    )
    mids, points = guidance_between_rails(
        left,
        right,
        pavement=pavement,
        obstacle=obstacle,
        lam=lam,
        margin=1.0,
    )
    stats = heading_curvature_stats(mids)
    if ufld_error:
        stats["ufld_error"] = ufld_error
    return OccupiedRails(
        left=left,
        right=right,
        mids=mids,
        points=points,
        paint_source=paint_source,
        left_kind=left_kind,
        right_kind=right_kind,
        stats=stats,
        paint_rails=quality_paint,
    )
