import json
from pathlib import Path

from app.path_manifest_export import export_session_path_manifest, manifest_to_jsonl


def test_export_session_path_manifest_combines_labels_and_prediction(tmp_path):
    session_dir = tmp_path / "session-demo"
    session_dir.mkdir()
    (session_dir / "manifest.jsonl").write_text(
        json.dumps(
            {
                "backend_saved_frame": "frames/frame-0001.jpg",
                "mode": "walking",
                "event": "sent_to_backend",
                "perception": {
                    "path_guidance": {
                        "near_path_status": "caution",
                        "left_front_status": "candidateOpen",
                        "right_front_status": "candidateOpen",
                        "focus_direction": "center",
                    }
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "labels.jsonl").write_text(
        json.dumps(
            {
                "frame": "frames/frame-0001.jpg",
                "label": "no_obvious_risk",
                "true_scene": "室内走廊",
                "true_risks": "无明显风险",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = export_session_path_manifest("demo", session_dir)

    assert rows[0]["frame_id"] == "demo/frames/frame-0001.jpg"
    assert rows[0]["ground_truth"]["near_path_status"] == "candidateOpen"
    assert rows[0]["prediction"]["near_path_status"] == "caution"
    assert "indoor" in rows[0]["scene_tags"]
    assert manifest_to_jsonl(rows).strip().startswith('{"frame_id"')
