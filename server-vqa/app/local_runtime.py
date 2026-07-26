"""Resolve and launch a directly-managed ``llama-server`` for local Qwen VQA.

We deliberately run ``llama-server`` (the binary bundled inside Ollama.app)
ourselves instead of letting Ollama spawn it, because Ollama derives
``--image-min-tokens`` from the model's baked vision config (1024 for
qwen2.5vl) and exposes no env var or Modelfile PARAMETER to lower it. Lowering
that floor is the single biggest prefill-latency lever on a 16GB Mac, so we
launch llama-server directly with our own ``--image-min-tokens`` value.

Ollama is still used purely as the *downloader* (``ollama pull`` populates the
blob store); this module only reads the manifest it wrote and resolves the
weight blobs on disk. Everything here is pure/deterministic so it can be unit
tested without a running server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

# qwen2.5-vl embeds the vision projector inside the model blob (there is no
# separate "projector" layer in the manifest), so ``--mmproj`` points at the
# same file as ``--model``. This constant documents that fallback.
_EMBEDDED_PROJECTOR = "model"


def manifest_path_for(model_ref: str, models_dir: Path) -> Path:
    """Map an Ollama model reference to its on-disk manifest file.

    ``qwen2.5vl:3b`` -> ``<models_dir>/manifests/registry.ollama.ai/library/qwen2.5vl/3b``
    A fully-qualified name (containing ``/``) is used as-is under ``manifests``.
    """
    if ":" in model_ref:
        name, tag = model_ref.split(":", 1)
    else:
        name, tag = model_ref, "latest"

    if not name:
        raise ValueError(f"invalid model reference: {model_ref!r}")

    registry_path = name if "/" in name else f"registry.ollama.ai/library/{name}"
    return models_dir / "manifests" / registry_path / tag


def resolve_blobs(manifest_file: Path, models_dir: Path) -> dict:
    """Read an Ollama manifest and return absolute paths to the model / mmproj blobs.

    Returns a dict ``{"model": <path>, "mmproj": <path>}``. When the manifest
    has no separate projector layer (qwen2.5-vl), ``mmproj`` falls back to the
    model blob so the embedded vision tower is used.
    """
    data = json.loads(manifest_file.read_text())

    model_blob: Path | None = None
    mmproj_blob: Path | None = None

    for layer in data.get("layers", []):
        media_type = layer.get("mediaType", "")
        kind = media_type.rsplit(".", 1)[-1]
        digest = layer.get("digest", "")
        if not digest:
            continue
        blob = models_dir / "blobs" / digest.replace("sha256:", "sha256-")
        if kind == "model":
            model_blob = blob
        elif kind == "projector":
            mmproj_blob = blob

    if model_blob is None:
        raise ValueError(f"no 'model' layer found in manifest {manifest_file}")

    if mmproj_blob is None:
        mmproj_blob = model_blob  # embedded projector

    return {"model": str(model_blob), "mmproj": str(mmproj_blob)}


def build_args(
    *,
    binary: str,
    model_blob: str,
    mmproj_blob: str,
    host: str,
    port: int,
    image_min_tokens: int,
    image_max_tokens: int,
) -> List[str]:
    """Build the full ``llama-server`` argv.

    ``--image-min-tokens`` is the whole point of managing the server ourselves,
    so it is always emitted explicitly.
    """
    if image_min_tokens <= 0:
        raise ValueError(f"image_min_tokens must be positive, got {image_min_tokens}")
    if image_max_tokens < image_min_tokens:
        raise ValueError(
            f"image_max_tokens ({image_max_tokens}) must be >= "
            f"image_min_tokens ({image_min_tokens})"
        )

    return [
        binary,
        "--model", model_blob,
        "--mmproj", mmproj_blob,
        "--image-min-tokens", str(image_min_tokens),
        "--image-max-tokens", str(image_max_tokens),
        "--host", host,
        "--port", str(port),
        "--no-webui",
    ]


def validate_blobs(blobs: dict) -> None:
    """Fail loudly if a resolved blob is missing on disk (No Silent Failures)."""
    for role, path in blobs.items():
        if not Path(path).is_file():
            raise FileNotFoundError(
                f"{role} blob not found: {path}. "
                f"Run `ollama pull <model>` to download it."
            )


def _resolve_argv(args: argparse.Namespace) -> List[str]:
    models_dir = Path(args.models_dir).expanduser()
    manifest = manifest_path_for(args.model, models_dir)
    if not manifest.is_file():
        raise FileNotFoundError(
            f"manifest not found: {manifest}. "
            f"Run `ollama pull {args.model}` first."
        )
    blobs = resolve_blobs(manifest, models_dir)
    validate_blobs(blobs)
    return build_args(
        binary=args.binary,
        model_blob=blobs["model"],
        mmproj_blob=blobs["mmproj"],
        host=args.host,
        port=args.port,
        image_min_tokens=args.image_min_tokens,
        image_max_tokens=args.image_max_tokens,
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve local llama-server launch args.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", required=True)
    common.add_argument("--models-dir", required=True)

    p_resolve = sub.add_parser("resolve", parents=[common], help="Print resolved blob paths as JSON.")
    p_resolve.set_defaults(_needs_launch=False)

    p_args = sub.add_parser("args", parents=[common], help="Print null-delimited llama-server argv.")
    p_args.add_argument("--binary", required=True)
    p_args.add_argument("--host", default="127.0.0.1")
    p_args.add_argument("--port", type=int, default=11435)
    p_args.add_argument("--image-min-tokens", type=int, default=256)
    p_args.add_argument("--image-max-tokens", type=int, default=512)
    p_args.set_defaults(_needs_launch=True)

    args = parser.parse_args(argv)

    if args.command == "resolve":
        models_dir = Path(args.models_dir).expanduser()
        manifest = manifest_path_for(args.model, models_dir)
        if not manifest.is_file():
            print(f"manifest not found: {manifest}", file=sys.stderr)
            return 1
        blobs = resolve_blobs(manifest, models_dir)
        validate_blobs(blobs)
        print(json.dumps(blobs))
        return 0

    # command == "args": emit null-delimited argv so blob paths with unusual
    # characters survive the shell round-trip.
    argv_out = _resolve_argv(args)
    sys.stdout.write("\0".join(argv_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
