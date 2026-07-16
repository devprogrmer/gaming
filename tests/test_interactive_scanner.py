from __future__ import annotations

import pytest

from gaming.interactive import pinger, scanner
from gaming.interactive.classify import GOOD, ProbeResult
from gaming.interactive.settings import Settings


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


def test_ping_host_aggregates_samples(monkeypatch):
    # Every ICMP probe returns 10ms -> 4/4 received, avg 10.
    monkeypatch.setattr(pinger, "_ping_once", lambda host, timeout: 10.0)
    result = pinger.ping_host("1.2.3.4", count=4, timeout=1.0)
    assert result.sent == 4
    assert result.received == 4
    assert result.avg_ms == 10.0
    assert result.min_ms == 10.0
    assert result.max_ms == 10.0


def test_ping_host_falls_back_to_tcp(monkeypatch):
    # ICMP always fails, TCP connect on first fallback port succeeds.
    monkeypatch.setattr(pinger, "_ping_once", lambda host, timeout: None)
    monkeypatch.setattr(pinger, "_tcp_once", lambda host, port, timeout: 5.0)
    result = pinger.ping_host("1.2.3.4", count=3, timeout=1.0)
    assert result.received == 1
    assert result.avg_ms == 5.0


def test_ping_host_dead(monkeypatch):
    monkeypatch.setattr(pinger, "_ping_once", lambda host, timeout: None)
    monkeypatch.setattr(pinger, "_tcp_once", lambda host, port, timeout: None)
    result = pinger.ping_host("1.2.3.4", count=2, timeout=1.0)
    assert result.received == 0
    assert result.reachable is False


def test_scan_hosts_invokes_callback(monkeypatch):
    monkeypatch.setattr(
        pinger,
        "ping_host",
        lambda h, count=4, timeout=2.0: ProbeResult(h, sent=count, received=count, avg_ms=10.0),
    )
    seen = []
    results = pinger.scan_hosts(
        ["1.1.1.1", "2.2.2.2"], count=2, on_result=lambda p: seen.append(p.host)
    )
    assert len(results) == 2
    assert set(seen) == {"1.1.1.1", "2.2.2.2"}
    # Input order preserved.
    assert [r.host for r in results] == ["1.1.1.1", "2.2.2.2"]


def test_run_scan_classifies_and_reports(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=20.0)),
    )
    report = scanner.run_scan("iran", Settings(), hosts=["1.1.1.1", "2.2.2.2"])
    assert report.total == 2
    assert report.counts[GOOD] == 2
    assert report.alive_hosts() == ["1.1.1.1", "2.2.2.2"]


def test_discover_alive_returns_only_reachable(monkeypatch):
    def probe(h):
        received = 1 if h == "1.1.1.1" else 0
        return ProbeResult(h, sent=1, received=received, avg_ms=10.0 if received else None)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(probe))
    alive = scanner.discover_alive("iran", Settings(), hosts=["1.1.1.1", "2.2.2.2"])
    assert alive == ["1.1.1.1"]


def test_persist_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=20.0)),
    )
    from gaming.interactive.storage import HistoryStore

    store = HistoryStore(tmp_path / "h.db")
    report = scanner.run_scan("foreign", Settings(), hosts=["9.9.9.9"])
    scan_id = scanner.persist(report, store)
    assert scan_id > 0
    assert store.get_results(scan_id)[0].host == "9.9.9.9"


def _fake_scan_hosts(probe_fn):
    """Return a stand-in for scan_hosts that runs probe_fn synchronously."""

    def _impl(hosts, *, count=4, timeout=2.0, concurrency=32, on_result=None):
        results = []
        for h in hosts:
            p = probe_fn(h)
            results.append(p)
            if on_result is not None:
                on_result(p)
        return results

    return _impl
