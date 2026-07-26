import json

import pytest

from app.local_runtime import (
    build_args,
    manifest_path_for,
    resolve_blobs,
    validate_blobs,
)


# --- manifest_path_for -------------------------------------------------------

def test_manifest_path_for_short_name_with_tag(tmp_path):
    path = manifest_path_for("qwen2.5vl:3b", tmp_path)
    assert path == tmp_path / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5vl" / "3b"


def test_manifest_path_for_defaults_tag_to_latest(tmp_path):
    path = manifest_path_for("qwen2.5vl", tmp_path)
    assert path.name == "latest"


def test_manifest_path_for_qualified_name_used_as_is(tmp_path):
    path = manifest_path_for("myhost.io/team/model:v2", tmp_path)
    assert path == tmp_path / "manifests" / "myhost.io" / "team" / "model" / "v2"


def test_manifest_path_for_rejects_empty_name(tmp_path):
    with pytest.raises(ValueError):
        manifest_path_for(":3b", tmp_path)


# --- resolve_blobs -----------------------------------------------------------

def _write_manifest(tmp_path, layers):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"layers": layers}))
    return manifest


def test_resolve_blobs_embedded_projector_falls_back_to_model(tmp_path):
    # qwen2.5-vl: single model layer, no separate projector.
    manifest = _write_manifest(
        tmp_path,
        [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:abc", "size": 10}],
    )
    blobs = resolve_blobs(manifest, tmp_path)
    assert blobs["model"].endswith("blobs/sha256-abc")
    # embedded projector: mmproj points at the same blob as model.
    assert blobs["mmproj"] == blobs["model"]


def test_resolve_blobs_uses_separate_projector_when_present(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [
            {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:aaa", "size": 10},
            {"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:bbb", "size": 5},
        ],
    )
    blobs = resolve_blobs(manifest, tmp_path)
    assert blobs["model"].endswith("sha256-aaa")
    assert blobs["mmproj"].endswith("sha256-bbb")


def test_resolve_blobs_raises_without_model_layer(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [{"mediaType": "application/vnd.ollama.image.license", "digest": "sha256:x", "size": 1}],
    )
    with pytest.raises(ValueError):
        resolve_blobs(manifest, tmp_path)


# --- build_args --------------------------------------------------------------

def test_build_args_always_emits_image_min_tokens():
    args = build_args(
        binary="/x/llama-server",
        model_blob="/blobs/model",
        mmproj_blob="/blobs/model",
        host="127.0.0.1",
        port=11435,
        image_min_tokens=256,
        image_max_tokens=512,
    )
    assert args[0] == "/x/llama-server"
    # the whole reason we manage the server ourselves:
    i = args.index("--image-min-tokens")
    assert args[i + 1] == "256"
    j = args.index("--image-max-tokens")
    assert args[j + 1] == "512"
    assert args[args.index("--port") + 1] == "11435"
    assert "--no-webui" in args


def test_build_args_rejects_nonpositive_min_tokens():
    with pytest.raises(ValueError):
        build_args(
            binary="s", model_blob="m", mmproj_blob="m",
            host="h", port=1, image_min_tokens=0, image_max_tokens=512,
        )


def test_build_args_rejects_max_below_min():
    with pytest.raises(ValueError):
        build_args(
            binary="s", model_blob="m", mmproj_blob="m",
            host="h", port=1, image_min_tokens=512, image_max_tokens=256,
        )


# --- validate_blobs ----------------------------------------------------------

def test_validate_blobs_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_blobs({"model": str(tmp_path / "nope")})


def test_validate_blobs_passes_when_present(tmp_path):
    blob = tmp_path / "present"
    blob.write_text("x")
    validate_blobs({"model": str(blob)})  # no raise
