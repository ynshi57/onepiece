import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.device_debug_api import router
from tools.device_debug_codex import (
    ClientError, DeviceDebugClient, NoRedirect, load_pairing, main, parser, run, summarize, validate_report, watch,
)


TOKEN = "local-test-pair-token"


class APIClient(DeviceDebugClient):
    """Exercise the actual router/store without binding a network port."""
    def __init__(self, http):
        super().__init__(token=TOKEN)
        self.http = http

    def _request(self, path, method="GET", payload=None, limit=8 * 1024 * 1024):
        response = self.http.request(method, "/device-debug" + path, json=payload,
                                     headers={"X-Device-Debug-Token": self.token})
        if response.status_code >= 400:
            raise ClientError(f"Backend returned HTTP {response.status_code}")
        if len(response.content) > limit:
            raise ClientError("Backend response exceeds the local read limit")
        return response.content


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VQASEE_DEVICE_DEBUG_ROOT", str(tmp_path / "backend"))
    monkeypatch.setenv("VQASEE_DEVICE_DEBUG_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as http:
        yield APIClient(http)


def create(client):
    return client.json("/sessions", "POST", {"label": "真机摄像头与按钮"})


def add_screen(client, sid):
    data = io.BytesIO()
    # This is a transport fixture, not a screenshot or visual acceptance evidence.
    Image.new("RGB", (4, 6), (12, 40, 80)).save(data, format="JPEG")
    response = client.http.post(f"/device-debug/sessions/{sid}/screen", content=data.getvalue(),
                                headers={"X-Device-Debug-Token": TOKEN, "X-Capture-Timestamp": "123.45"})
    assert response.status_code == 200
    return response.json(), data.getvalue()


def test_inspect_events_and_verified_evidence_download(client, tmp_path):
    sid = create(client)["id"]
    evidence, original = add_screen(client, sid)
    event = client.json(f"/sessions/{sid}/events", "POST", {"kind": "capture_button", "details": {"visible": True}})
    summary = run(parser().parse_args(["inspect", sid]), client)
    assert summary["screen_count"] == 1
    assert summary["event_count"] == 1
    assert summary["analysis"] == "not_performed"
    assert "no guaranteed shared clock" in summary["clock_note"]
    events = run(parser().parse_args(["events", sid]), client)
    assert events["events"][0]["id"] == event["id"]
    output = tmp_path / "screen.jpg"
    result = client.download(sid, evidence["id"], output)
    assert output.read_bytes() == original
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["integrity"] == "verified"
    with pytest.raises(FileExistsError):
        client.download(sid, evidence["id"], output)
    assert output.read_bytes() == original


def test_corrupt_evidence_does_not_create_local_file(client, tmp_path, monkeypatch):
    sid = create(client)["id"]
    evidence, _ = add_screen(client, sid)
    original_request = client._request
    def corrupted(path, method="GET", payload=None, limit=8 * 1024 * 1024):
        if "/files/" in path:
            return b"x" * evidence["bytes"]
        return original_request(path, method, payload, limit)
    monkeypatch.setattr(client, "_request", corrupted)
    destination = tmp_path / "corrupt.jpg"
    with pytest.raises(ClientError, match="checksum"):
        client.download(sid, evidence["id"], destination)
    assert not destination.exists()


def test_report_and_retest_round_trip_keep_hypothesis_status(client, tmp_path):
    sid = create(client)["id"]
    event = client.json(f"/sessions/{sid}/events", "POST", {"kind": "camera_paused", "details": {}})
    report = {"summary": "测试报告；非真机根因结论", "findings": [{
        "problem": "暂停后画面不再更新", "root_cause": "可能未恢复会话；需检查生命周期",
        "status": "hypothesis", "evidence_ids": [event["id"]],
        "proposal": "记录前后台切换事件并复测画面刷新"}], "retest_session_ids": []}
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    submitted = run(parser().parse_args(["report-submit", sid, "--file", str(report_path)]), client)
    assert submitted["findings"][0]["status"] == "hypothesis"
    retest = run(parser().parse_args(["retest-create", sid, "--label", "恢复后复测"]), client)
    assert retest["previous_session_id"] == sid
    linked = run(parser().parse_args(["retest-link", sid, retest["id"]]), client)
    assert linked["retest_session_ids"] == [retest["id"]]
    assert linked["findings"] == report["findings"]
    # Idempotent linking must not duplicate the same retest.
    linked = run(parser().parse_args(["retest-link", sid, retest["id"]]), client)
    assert linked["retest_session_ids"] == [retest["id"]]


def test_unrelated_session_cannot_be_linked_as_retest(client):
    original, unrelated = create(client), create(client)
    with pytest.raises(ClientError, match="previous_session_id"):
        run(parser().parse_args(["retest-link", original["id"], unrelated["id"]]), client)


def test_confirmed_requires_real_session_evidence(client):
    session = create(client)
    finding = {"problem": "按钮不可见", "root_cause": "布局遮挡", "status": "confirmed", "evidence_ids": [], "proposal": "修复后看图"}
    report = {"summary": "测试", "findings": [finding], "retest_session_ids": []}
    with pytest.raises(ClientError, match="require evidence"):
        validate_report(report, session)
    finding["evidence_ids"] = [str(uuid4())]
    with pytest.raises(ClientError, match="outside this session"):
        validate_report(report, session)


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1@evil.example", "http://localhost/path", "http://127.0.0.1?token=secret"])
def test_pair_token_is_restricted_to_local_backend(url):
    with pytest.raises(ClientError, match="loopback"):
        DeviceDebugClient(url, token=TOKEN)


def test_redirect_is_not_followed():
    with pytest.raises(ClientError, match="Redirect refused"):
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_report_template_needs_no_secret_and_makes_no_claim(monkeypatch, capsys):
    monkeypatch.delenv("VQASEE_DEVICE_DEBUG_TOKEN", raising=False)
    assert main(["report-template"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["findings"] == []
    assert "尚未分析" in report["summary"]


def test_private_pairing_file_supplies_launcher_credentials(tmp_path, monkeypatch, capsys):
    pairing = tmp_path / "pairing.json"
    pairing.write_text(json.dumps({"url": "http://127.0.0.1:9001", "token": TOKEN}))
    pairing.chmod(0o600)
    monkeypatch.delenv("VQASEE_DEVICE_DEBUG_TOKEN", raising=False)
    seen = {}
    def fake_run(args, client):
        seen.update(url=client.base_url, token=client.token)
        return []
    monkeypatch.setattr("tools.device_debug_codex.run", fake_run)
    assert main(["--pairing-file", str(pairing), "list"]) == 0
    assert seen == {"url": "http://127.0.0.1:9001/device-debug", "token": TOKEN}
    assert TOKEN not in capsys.readouterr().out


def test_pairing_rejects_shared_permissions_and_symlinks(tmp_path):
    pairing = tmp_path / "pairing.json"
    pairing.write_text(json.dumps({"url": "http://127.0.0.1:9001", "token": TOKEN}))
    pairing.chmod(0o644)
    with pytest.raises(ClientError, match="private"):
        load_pairing(pairing)
    pairing.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(pairing)
    with pytest.raises(OSError):
        load_pairing(link)


def test_watch_cursor_returns_only_new_evidence_and_events(client):
    sid = create(client)["id"]
    first, _ = add_screen(client, sid)
    initial = watch(client, sid, timeout=0)
    assert initial["new_screen_count"] == 1
    assert initial["evidence"][0]["id"] == first["id"]
    assert initial["analysis"] == "not_performed"
    second, _ = add_screen(client, sid)
    event = client.json(f"/sessions/{sid}/events", "POST", {"kind": "button_pressed", "details": {}})
    next_result = watch(client, sid, timeout=0, cursor=initial["cursor"])
    assert [item["id"] for item in next_result["evidence"]] == [second["id"]]
    assert [item["id"] for item in next_result["events"]] == [event["id"]]
    assert client.session(sid)["report"] is None
    empty = watch(client, sid, timeout=0, cursor=next_result["cursor"])
    assert empty["events"] == empty["evidence"] == []
    assert empty["timed_out"] is True
    assert empty["changed"] is False
    assert client.timeout == 15


def test_watch_stops_and_rejects_cross_session_cursor(client):
    sid = create(client)["id"]
    checkpoint = watch(client, sid, timeout=0)
    with pytest.raises(ClientError, match="cursor"):
        watch(client, create(client)["id"], timeout=0, cursor=checkpoint["cursor"])
    client.json(f"/sessions/{sid}/stop", "POST", {"reason": "用户停止"})
    stopped = watch(client, sid, timeout=30, cursor=checkpoint["cursor"])
    assert stopped["stream_state"] == "stopped"
    assert stopped["state_changed"] is True
    assert stopped["timed_out"] is False


@pytest.mark.parametrize("timeout", [-1, 30.1, float("nan"), float("inf")])
def test_watch_has_finite_bounded_timeout(client, timeout):
    with pytest.raises(ClientError, match="between 0 and 30"):
        watch(client, str(uuid4()), timeout=timeout)
