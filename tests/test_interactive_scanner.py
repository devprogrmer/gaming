from __future__ import annotations

import pytest

from gaming.interactive import pinger, scanner
from gaming.interactive.classify import GOOD, ProbeResult
from gaming.interactive.settings import Settings


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


@pytest.fixture(autouse=True)
def _no_real_global(monkeypatch):
    """Keep the abroad pass offline by default.

    ``run_scan`` now runs a check-host.net abroad pass (Settings.check_global
    defaults on). Stub it to "not checked" so the bulk of the scanner tests stay
    deterministic and network-free; the dedicated bidirectional tests below
    override this with their own stub.
    """
    monkeypatch.setattr(
        scanner, "check_abroad", lambda host, **kw: (None, 0, 0)
    )
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


# ---- lowest-latency grouping (Iran-origin reporting) ---------------------
def _rec(country=None, provider=None):
    from gaming.models import IPRecord

    return IPRecord(prefix="1.2.3.0/24", country=country, provider=provider)


def test_summarize_by_group_picks_lowest_latency_country():
    from gaming.interactive.classify import BAD

    h2r = {
        "1.1.1.1": _rec("DE", "hetzner"),
        "2.2.2.2": _rec("NL", "leaseweb"),
        "3.3.3.3": _rec("DE", "hetzner"),
    }
    results = [
        (ProbeResult("1.1.1.1", sent=4, received=4, avg_ms=40.0), GOOD),
        (ProbeResult("2.2.2.2", sent=4, received=4, avg_ms=120.0), GOOD),
        (ProbeResult("3.3.3.3", sent=4, received=0), BAD),
    ]
    groups = scanner.summarize_by_group(results, h2r)
    # DE (40ms live) sorts ahead of NL (120ms).
    assert groups[0].key == "DE"
    assert groups[0].avg_ms == 40.0
    assert groups[0].live == 1
    assert groups[0].total == 2


def test_summarize_by_group_unknown_bucket():
    results = [(ProbeResult("9.9.9.9", sent=4, received=0), "BAD")]
    groups = scanner.summarize_by_group(results, {})
    assert groups[0].key == "unknown"
    assert groups[0].avg_ms is None


def test_summarize_by_group_by_provider():
    h2r = {"1.1.1.1": _rec("US", "cloudflare")}
    results = [(ProbeResult("1.1.1.1", sent=4, received=4, avg_ms=10.0), GOOD)]
    groups = scanner.summarize_by_group(results, h2r, by="provider")
    assert groups[0].key == "cloudflare"


# ---- bidirectional (Iran + abroad) reachability --------------------------
from gaming.interactive.classify import (  # noqa: E402
    ABROAD_ONLY,
    INTERNATIONAL,
    IRAN_ONLY,
    UNREACHABLE,
)


def _bidi_settings(**over):
    base = dict(check_global=True, max_global_targets=10)
    base.update(over)
    return Settings(**base)


def test_run_scan_combined_all_four_verdicts(monkeypatch):
    # Local: A & B answer, C & D dead. Abroad: A & C ok, B & D fail.
    local_alive = {"a": True, "b": True, "c": False, "d": False}
    abroad = {"a": (True, 2, 2), "b": (False, 0, 2), "c": (True, 2, 2), "d": (False, 0, 2)}

    def probe(h):
        recv = 4 if local_alive[h] else 0
        return ProbeResult(h, sent=4, received=recv, avg_ms=20.0 if recv else None)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(probe))
    monkeypatch.setattr(scanner, "check_abroad", lambda host, **kw: abroad[host])

    report = scanner.run_scan("iran", _bidi_settings(), hosts=["a", "b", "c", "d"])
    verdicts = {c.host: report.combined_verdict(c) for c in report.combined}
    assert verdicts == {
        "a": INTERNATIONAL,
        "b": IRAN_ONLY,
        "c": ABROAD_ONLY,
        "d": UNREACHABLE,
    }
    counts = report.combined_counts
    assert counts[INTERNATIONAL] == 1
    assert counts[IRAN_ONLY] == 1
    assert counts[ABROAD_ONLY] == 1
    assert counts[UNREACHABLE] == 1


def test_run_scan_abroad_disabled_is_not_checked(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=10.0)),
    )
    # If check_global is off, global_reachability must not even be called.
    def _boom(host, **kw):
        raise AssertionError("abroad check should be skipped when disabled")

    monkeypatch.setattr(scanner, "check_abroad", _boom)
    report = scanner.run_scan(
        "iran", _bidi_settings(check_global=False), hosts=["1.1.1.1"]
    )
    c = report.combined[0]
    assert c.abroad_reachable is None
    assert report.combined_verdict(c) == IRAN_ONLY  # alive locally, abroad unknown


def test_run_scan_abroad_failure_does_not_break_local(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )

    def _explode(host, **kw):
        raise RuntimeError("check-host.net down")

    monkeypatch.setattr(scanner, "check_abroad", _explode)
    report = scanner.run_scan("iran", _bidi_settings(), hosts=["1.1.1.1", "2.2.2.2"])
    # Local results intact; scan not aborted.
    assert report.total == 2
    assert report.counts[GOOD] == 2
    # Abroad stayed "not checked" because every abroad call raised.
    assert all(c.abroad_reachable is None for c in report.combined)


def test_run_scan_abroad_cap_prioritises_alive(monkeypatch):
    # Two alive, two dead; cap of 2 -> only the alive hosts get an abroad check.
    alive = {"a": True, "b": True, "c": False, "d": False}
    checked: list[str] = []

    def probe(h):
        recv = 4 if alive[h] else 0
        return ProbeResult(h, sent=4, received=recv, avg_ms=10.0 if recv else None)

    def _record(host, **kw):
        checked.append(host)
        return (True, 2, 2)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(probe))
    monkeypatch.setattr(scanner, "check_abroad", _record)
    scanner.run_scan(
        "iran", _bidi_settings(max_global_targets=2), hosts=["c", "a", "d", "b"]
    )
    assert set(checked) == {"a", "b"}  # alive-first, dead hosts skipped by the cap


def test_persist_roundtrip_stores_combined(monkeypatch, tmp_path):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=20.0)),
    )
    monkeypatch.setattr(scanner, "check_abroad", lambda host, **kw: (True, 3, 4))
    from gaming.interactive.storage import HistoryStore

    store = HistoryStore(tmp_path / "h.db")
    report = scanner.run_scan("foreign", _bidi_settings(), hosts=["9.9.9.9"])
    scan_id = scanner.persist(report, store)
    row = store.get_results(scan_id)[0]
    assert row.host == "9.9.9.9"
    assert row.abroad_reachable is True
    assert row.abroad_nodes_ok == 3
    assert row.abroad_nodes_total == 4
    assert row.combined_verdict == INTERNATIONAL


# ---- common-ports scan (Part C1) ----------------------------------------
def _port_settings(**over):
    base = dict(check_global=False, scan_ports=True, ports="80,443")
    base.update(over)
    return Settings(**base)


def test_run_scan_populates_open_ports_for_alive_hosts(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=20.0)),
    )
    monkeypatch.setattr(
        scanner, "probe_ports", lambda host, ports, **kw: [p for p in ports if p == 443]
    )
    report = scanner.run_scan("iran", _port_settings(), hosts=["1.1.1.1"])
    assert report.combined[0].open_ports == [443]


def test_run_scan_skips_ports_for_dead_hosts(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=0)),
    )
    called: list[str] = []

    def _record(host, ports, **kw):
        called.append(host)
        return list(ports)

    monkeypatch.setattr(scanner, "probe_ports", _record)
    report = scanner.run_scan("iran", _port_settings(), hosts=["1.1.1.1"])
    assert called == []  # dead host never gets a port probe
    assert report.combined[0].open_ports == []


def test_run_scan_ports_disabled_does_not_probe(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=20.0)),
    )

    def _boom(host, ports, **kw):
        raise AssertionError("port scan should be skipped when disabled")

    monkeypatch.setattr(scanner, "probe_ports", _boom)
    report = scanner.run_scan(
        "iran", _port_settings(scan_ports=False), hosts=["1.1.1.1"]
    )
    assert report.combined[0].open_ports == []


def test_run_scan_port_failure_does_not_break_scan(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=20.0)),
    )

    def _explode(host, ports, **kw):
        raise RuntimeError("socket exhausted")

    monkeypatch.setattr(scanner, "probe_ports", _explode)
    report = scanner.run_scan("iran", _port_settings(), hosts=["1.1.1.1", "2.2.2.2"])
    # Scan completes; ports simply stay empty.
    assert report.total == 2
    assert all(c.open_ports == [] for c in report.combined)


def test_settings_port_list_parsing():
    s = Settings(ports="80, 443 ,80,99999,abc,22")
    # de-duplicated, out-of-range/junk dropped, order preserved
    assert s.port_list() == [80, 443, 22]
    # clamped() normalises the stored string too
    assert s.clamped().ports == "80,443,22"
