"""Regression tests for Bonjour/mDNS LAN advertising.

Root cause of the 2026-08-24 startup crash: zeroconf.register_service() raised
EventLoopBlocked inside the FastAPI lifespan and nothing caught it, so the whole
server refused to start. LAN auto-advertise is a best-effort convenience and must
never be able to take the server down. These tests lock that contract in.
"""

import zeroconf as zeroconf_module

from app.discovery import BonjourAdvertiser


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


def test_disable_env_short_circuits_before_touching_zeroconf(monkeypatch):
    _FakeZeroconf.instances.clear()
    monkeypatch.setenv("VQASEE_DISABLE_BONJOUR", "1")
    monkeypatch.setattr(zeroconf_module, "Zeroconf", _FakeZeroconf)

    advertiser = BonjourAdvertiser()
    advertiser.start(port=9000)

    # Disabled path never constructs a Zeroconf at all.
    assert _FakeZeroconf.instances == []
    assert advertiser._zeroconf is None
