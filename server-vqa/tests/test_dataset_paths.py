import json
from pathlib import Path

from app.dataset_paths import (
    camvid_relative_tail,
    open_dataset_root,
    portable_dataset_path,
    remap_stale_dataset_path,
    repo_root,
    resolve_dataset_file,
    rewrite_manifest_resolved_paths,
)


def test_open_dataset_root_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    assert open_dataset_root() == tmp_path.resolve()


def test_open_dataset_root_defaults_to_repo_dataset(monkeypatch):
    monkeypatch.delenv("VQASEE_DATASET_ROOT", raising=False)
    assert open_dataset_root() == (repo_root() / "dataset").resolve()


def test_camvid_relative_tail_extracts_rgb_and_label():
    stale = "/Users/bayes/.cache/vqasee/open-datasets/camvid/CamVid_RGB/0001TP_006690.png"
    assert camvid_relative_tail(stale) == "CamVid_RGB/0001TP_006690.png"
    label = "/Users/bayes/.cache/vqasee/open-datasets/camvid/CamVid_Label/0001TP_006690_L.png"
    assert camvid_relative_tail(label) == "CamVid_Label/0001TP_006690_L.png"


def test_remap_stale_bayes_path_onto_current_root(tmp_path):
    root = tmp_path / "dataset"
    stale = "/Users/bayes/.cache/vqasee/open-datasets/camvid/CamVid_RGB/0001TP_006690.png"
    remapped = remap_stale_dataset_path(stale, dataset_root=root)
    assert remapped == (root / "camvid" / "CamVid_RGB" / "0001TP_006690.png").resolve()


def test_resolve_dataset_file_finds_remapped_camvid_image(tmp_path):
    root = tmp_path / "dataset"
    image = root / "camvid" / "CamVid_RGB" / "0001TP_006690.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    stale = "/Users/bayes/.cache/vqasee/open-datasets/camvid/CamVid_RGB/0001TP_006690.png"
    found = resolve_dataset_file(stale, dataset_root=root, repo=tmp_path)
    assert found == image.resolve()


def test_resolve_dataset_file_accepts_repo_relative_path(tmp_path):
    image = tmp_path / "dataset" / "camvid" / "CamVid_RGB" / "frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    found = resolve_dataset_file(
        "dataset/camvid/CamVid_RGB/frame.png",
        dataset_root=tmp_path / "dataset",
        repo=tmp_path,
    )
    assert found == image.resolve()


def test_portable_dataset_path_is_repo_relative_inside_checkout(tmp_path, monkeypatch):
    fake_repo = tmp_path / "repo"
    target = fake_repo / "dataset" / "camvid" / "CamVid_RGB" / "foo.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    monkeypatch.setattr("app.dataset_paths.repo_root", lambda: fake_repo)
    assert portable_dataset_path(target, repo=fake_repo) == "dataset/camvid/CamVid_RGB/foo.png"


def test_portable_dataset_path_stays_absolute_outside_repo(tmp_path):
    outside = tmp_path / "elsewhere" / "frame.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    assert portable_dataset_path(outside, repo=tmp_path / "repo") == str(outside.resolve())


def test_committed_camvid_manifests_are_portable():
    repo = Path(__file__).resolve().parents[2]
    for name in (
        "camvid-manifest.jsonl",
        "camvid-manifest-walk.jsonl",
        "camvid-manifest-drive.jsonl",
        "camvid-manifest-test.jsonl",
        "camvid-manifest-walk-test.jsonl",
        "camvid-manifest-drive-test.jsonl",
    ):
        text = (repo / "docs" / "datasets" / name).read_text(encoding="utf-8")
        assert "/Users/bayes" not in text
        assert "dataset/camvid/CamVid_RGB/" in text
        assert "dataset/camvid/CamVid_Label/" in text
        assert "/Users/" not in text


def test_rewrite_manifest_resolves_repo_relative_camvid(tmp_path):
    repo = tmp_path / "repo"
    image = repo / "dataset" / "camvid" / "CamVid_RGB" / "frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    src = tmp_path / "camvid-manifest.jsonl"
    src.write_text(
        '{"frame_id":"f1","image_path":"dataset/camvid/CamVid_RGB/frame.png"}\n',
        encoding="utf-8",
    )
    dst = tmp_path / "resolved.jsonl"
    stats = rewrite_manifest_resolved_paths(
        src,
        dst,
        dataset_root=repo / "dataset",
        repo=repo,
    )
    assert stats == {"rows": 1, "resolved": 1, "missing": []}
    row = json.loads(dst.read_text(encoding="utf-8").strip())
    assert row["image_path"] == str(image.resolve())


def test_rewrite_manifest_counts_unresolved_paths(tmp_path):
    src = tmp_path / "camvid-manifest.jsonl"
    src.write_text(
        '{"frame_id":"f1","image_path":"dataset/camvid/CamVid_RGB/missing.png"}\n',
        encoding="utf-8",
    )
    dst = tmp_path / "resolved.jsonl"
    stats = rewrite_manifest_resolved_paths(
        src,
        dst,
        dataset_root=tmp_path / "dataset",
        repo=tmp_path,
    )
    assert stats["rows"] == 1
    assert stats["resolved"] == 0
    assert stats["missing"] == ["dataset/camvid/CamVid_RGB/missing.png"]

