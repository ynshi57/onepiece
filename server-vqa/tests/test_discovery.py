"""Regression tests for Bonjour/mDNS LAN advertising.

Root cause of the 2026-08-24 startup crash: zeroconf.register_service() raised
EventLoopBlocked inside the FastAPI lifespan and nothing caught it, so the whole
server refused to start. LAN auto-advertise is a best-effort convenience and must
never be able to take the server down. These tests lock that contract in.
"""

import zeroconf as zeroconf_module

from app.discovery import BonjourAdvertiser, _get_interface_ipv4, _get_lan_ip


class _FakeZeroconf:
    """Stand-in that simulates a wedged mDNS stack: register raises, like the
    real EventLoopBlocked / TimeoutError seen in production."""

    instances = []

    def __init__(self) -> None:
        self.closed = False
        _FakeZeroconf.instances.append(self)

    def register_service(self, service_info) -> None:
        raise RuntimeError("simulated EventLoopBlocked")

    def close(self) -> None:
        self.closed = True


def test_registration_failure_is_non_fatal_and_cleans_up(monkeypatch, capsys):
    _FakeZeroconf.instances.clear()
    monkeypatch.delenv("VQASEE_DISABLE_BONJOUR", raising=False)
    monkeypatch.setattr(zeroconf_module, "Zeroconf", _FakeZeroconf)

    advertiser = BonjourAdvertiser()

    # The whole point: a failing register_service must NOT raise out of start().
    advertiser.start(port=9000)

    # No live handle retained, so stop() is a no-op and there is nothing to leak.
    assert advertiser._zeroconf is None
    assert advertiser._service_info is None

    # The partially-created zeroconf instance was closed (no dangling sockets).
    assert len(_FakeZeroconf.instances) == 1
    assert _FakeZeroconf.instances[0].closed is True

    # Failure is surfaced loudly (no silent failure), with a recovery hint.
    out = capsys.readouterr().out
    assert "Bonjour discovery unavailable" in out
    assert "manual IP or relay" in out

    # stop() after a failed start must also be safe.
    advertiser.stop()


def test_get_lan_ip_prefers_en0_over_route(monkeypatch):
    def fake_run(args, **kwargs):
        class Result:
            returncode = 0
            stdout = ""

        if args[:2] == ["ipconfig", "getifaddr"]:
            interface = args[2]
            if interface == "en0":
                Result.stdout = "192.168.5.10"
                return Result()
            Result.returncode = 1
            return Result()
        raise AssertionError(f"unexpected subprocess.run: {args}")

    monkeypatch.setattr("app.discovery.subprocess.run", fake_run)
    monkeypatch.setattr("app.discovery._route_based_lan_ip", lambda: "10.8.0.2")

    assert _get_lan_ip() == "192.168.5.10"


def test_get_interface_ipv4_ignores_loopback(monkeypatch):
    class Result:
        returncode = 0
        stdout = "127.0.0.1"

    monkeypatch.setattr("app.discovery.subprocess.run", lambda *a, **k: Result())

    assert _get_interface_ipv4("en0") is None


def test_bonjour_properties_include_ip_when_lan_known(monkeypatch):
    captured = {}

    class FakeServiceInfo:
        def __init__(self, service_type, name, addresses, port, properties, server):
            captured["properties"] = properties
            captured["addresses"] = addresses

    class FakeZeroconf:
        def register_service(self, service_info):
            return None

        def close(self):
            return None

    monkeypatch.delenv("VQASEE_DISABLE_BONJOUR", raising=False)
    monkeypatch.setattr("app.discovery._get_lan_ip", lambda: "192.168.5.10")
    monkeypatch.setattr(zeroconf_module, "ServiceInfo", FakeServiceInfo)
    monkeypatch.setattr(zeroconf_module, "Zeroconf", FakeZeroconf)

    advertiser = BonjourAdvertiser()
    advertiser.start(port=9000)

    assert captured["properties"]["ip"] == "192.168.5.10"
    assert captured["properties"]["path"] == "/ws/signaling"
    assert len(captured["addresses"]) == 1


def test_disable_env_short_circuits_before_touching_zeroconf(monkeypatch):
    _FakeZeroconf.instances.clear()
    monkeypatch.setenv("VQASEE_DISABLE_BONJOUR", "1")
    monkeypatch.setattr(zeroconf_module, "Zeroconf", _FakeZeroconf)

    advertiser = BonjourAdvertiser()
    advertiser.start(port=9000)

    # Disabled path never constructs a Zeroconf at all.
    assert _FakeZeroconf.instances == []
    assert advertiser._zeroconf is None
