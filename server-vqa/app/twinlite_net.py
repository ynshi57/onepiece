"""TwinLiteNet BDD pretrained: RGB → drivable-area + lane-line masks.

Unlabeled product path. Does not read CamVid labels. Weights and architecture
live in ~/.cache/vqasee (same convention as UFLDv2). Official demo uses
640×360, /255, no ImageNet norm, CPU is fine.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np
from PIL import Image

_RUNTIME: dict[str, object] = {}
LAST_ERROR: str | None = None

_NET_W = 640
_NET_H = 360


def default_twinlite_repo() -> Path | None:
    env = os.environ.get("VQASEE_TWINLITE_REPO")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".cache" / "vqasee" / "ext" / "TwinLiteNet")
    for path in candidates:
        if (path / "model" / "TwinLite.py").is_file():
            return path
    return None


def default_twinlite_weights() -> Path | None:
    env = os.environ.get("VQASEE_TWINLITE_WEIGHTS")
    candidates = []
    if env:
        candidates.append(Path(env))
    root = Path.home() / ".cache" / "vqasee" / "models"
    candidates.append(root / "twinlitenet_bdd.pth")
    candidates.append(root / "best.pth")
    repo = default_twinlite_repo()
    if repo is not None:
        candidates.append(repo / "pretrained" / "best.pth")
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size >= 1_000_000:
                return path
        except OSError:
            continue
    return None


def _load_module(repo: Path):
    path = repo / "model" / "TwinLite.py"
    spec = importlib.util.spec_from_file_location("vqasee_twinlite_arch", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_module(state: dict) -> dict:
    if not state:
        return state
    if all(key.startswith("module.") for key in state):
        return {key[len("module.") :]: value for key, value in state.items()}
    return state


def infer_twinlite_masks(rgb: np.ndarray | None) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (lane, drivable) bool masks at the original image size."""
    global LAST_ERROR
    LAST_ERROR = None
    if rgb is None:
        LAST_ERROR = "no-rgb"
        return None
    repo = default_twinlite_repo()
    weights = default_twinlite_weights()
    if repo is None:
        LAST_ERROR = "missing-repo"
        return None
    if weights is None:
        LAST_ERROR = "missing-weights"
        return None
    try:
        import torch

        cache = _RUNTIME
        if cache.get("weights") != str(weights) or cache.get("repo") != str(repo):
            mod = _load_module(repo)
            if mod is None:
                LAST_ERROR = "import-failed"
                return None
            net = mod.TwinLiteNet()
            state = torch.load(str(weights), map_location="cpu")
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            net.load_state_dict(_strip_module(state))
            net.eval()
            cache.clear()
            cache.update(weights=str(weights), repo=str(repo), net=net)
        net = cache["net"]
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
        pil = Image.fromarray(np.asarray(rgb)).convert("RGB").resize((_NET_W, _NET_H), Image.Resampling.BILINEAR)
        tensor = torch.from_numpy(np.transpose(np.asarray(pil, dtype=np.float32) / 255.0, (2, 0, 1))[None, ...])
        with torch.no_grad():
            da_logits, ll_logits = net(tensor)
            da = da_logits.argmax(1)[0].cpu().numpy().astype(bool)
            lane = ll_logits.argmax(1)[0].cpu().numpy().astype(bool)
        da_img = Image.fromarray(da.astype(np.uint8) * 255).resize((width, height), Image.Resampling.NEAREST)
        lane_img = Image.fromarray(lane.astype(np.uint8) * 255).resize((width, height), Image.Resampling.NEAREST)
        return np.asarray(lane_img) > 0, np.asarray(da_img) > 0
    except Exception as exc:
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None
