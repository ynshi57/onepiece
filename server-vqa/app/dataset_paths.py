"""Portable dataset locations for VQASee (CamVid and other open datasets).

Default on-disk root is ``<repo>/dataset`` so a new machine does not inherit
another user's home directory (historically ``/Users/bayes/.cache/...`` got
baked into committed manifests via ``Path.resolve()``). Override with
``VQASEE_DATASET_ROOT`` when needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


_CAMVID_MARKERS = ("CamVid_RGB/", "CamVid_Label/")


def repo_root() -> Path:
    # server-vqa/app/dataset_paths.py → repo root is two parents up from app/.
    return Path(__file__).resolve().parents[2]


def open_dataset_root() -> Path:
    configured = os.getenv("VQASEE_DATASET_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (repo_root() / "dataset").resolve()


def camvid_relative_tail(path_text: str) -> str | None:
    """Return ``CamVid_RGB/foo.png`` (or Label) if that marker exists in the path."""
    posix = (path_text or "").replace("\\", "/")
    for marker in _CAMVID_MARKERS:
        idx = posix.find(marker)
        if idx >= 0:
            return posix[idx:]
    return None


def remap_stale_dataset_path(path_text: str, *, dataset_root: Path) -> Path | None:
    """Map a machine-specific CamVid path onto the current dataset root.

    Committed manifests used to store ``str(path.resolve())``, so they still
    point at ``/Users/bayes/.cache/vqasee/open-datasets/camvid/...``. The file
    name and CamVid folder are stable; only the prefix is stale.
    """
    tail = camvid_relative_tail(path_text)
    if tail:
        return (dataset_root / "camvid" / tail).resolve()
    posix = (path_text or "").replace("\\", "/")
    needle = "/camvid/"
    idx = posix.lower().find(needle)
    if idx >= 0:
        return (dataset_root / posix[idx + 1 :]).resolve()
    return None


def portable_dataset_path(path: Path, *, repo: Path | None = None) -> str:
    """Store repo-relative paths when the file lives inside this checkout."""
    resolved = path.expanduser().resolve()
    root = repo if repo is not None else repo_root()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def resolve_dataset_file(
    path_text: str,
    *,
    dataset_root: Path,
    repo: Path,
) -> Path | None:
    """Return an existing file, remapping stale absolute CamVid paths if needed."""
    text = (path_text or "").strip()
    if not text:
        return None
    raw = Path(text).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(repo / raw)
        candidates.append(Path.cwd() / raw)
    remapped = remap_stale_dataset_path(text, dataset_root=dataset_root)
    if remapped is not None:
        candidates.append(remapped)
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def rewrite_manifest_resolved_paths(
    src: Path,
    dst: Path,
    *,
    dataset_root: Path,
    repo: Path,
) -> dict:
    """Write ``dst`` with ``image_path`` rewritten to existing files.

    Older PerceptionHarness binaries joined repo-relative ``dataset/camvid/...``
    onto the manifest directory (``docs/datasets/dataset/camvid/...``). Resolving
    here keeps Python as the single path source of truth for the diagnostics
    launch path.
    """
    rows = 0
    resolved = 0
    missing: list[str] = []
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open(encoding="utf-8") as handle, dst.open("w", encoding="utf-8") as out:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            rows += 1
            raw = row.get("image_path") or row.get("image") or ""
            found = resolve_dataset_file(str(raw), dataset_root=dataset_root, repo=repo)
            if found is None:
                if len(missing) < 5:
                    missing.append(str(raw))
            else:
                row["image_path"] = str(found)
                resolved += 1
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"rows": rows, "resolved": resolved, "missing": missing}
