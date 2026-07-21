from __future__ import annotations

import pytest

from gaming.interactive import alerts, scanner, scheduler
from gaming.interactive.classify import ProbeResult
from gaming.interactive.settings import Settings
from gaming.interactive.storage import HistoryStore


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


def _seed_scan(store, scope, host_to_verdict):
    """Persist a scan where each host maps to a combined verdict directly."""
    from gaming.interactive.classify import CombinedResult

    results = []
    combined = []
    for host, verdict in host_to_verdict.items():
        # Local reachable unless verdict says otherwise; abroad set to force verdict.
        iran = verdict in ("INTERNATIONAL", "IRAN_ONLY")
        abroad = True if verdict in ("INTERNATIONAL", "ABROAD_ONLY") else (
            False if verdict in ("IRAN_ONLY", "UNREACHABLE") else None
        )
        probe = ProbeResult(host, sent=4, received=4 if iran else 0, avg_ms=10.0 if iran else None)
        results.append((probe, "GOOD" if iran else "BAD"))
        combined.append(
            CombinedResult(
                probe=probe,
                abroad_reachable=abroad,
                abroad_nodes_ok=2 if abroad else 0,
                abroad_nodes_total=2,
            )
        )
    return store.save_scan(scope, results, combined=combined)


# ---- alerts.diff_last_two -------------------------------------------------
def test_diff_detects_lost_and_gained_whitelist(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    _seed_scan(store, "iran", {"a": "INTERNATIONAL", "b": "IRAN_ONLY", "c": "UNREACHABLE"})
    _seed_scan(store, "iran", {"a": "IRAN_ONLY", "b": "IRAN_ONLY", "c": "INTERNATIONAL"})

    changes = {c.host: c for c in alerts.diff_last_two(store, "iran")}
    assert set(changes) == {"a", "c"}  # b unchanged
    assert changes["a"].lost_whitelist is True
    assert changes["c"].gained_whitelist is True


def test_diff_needs_two_scans(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    _seed_scan(store, "iran", {"a": "INTERNATIONAL"})
    assert alerts.diff_last_two(store, "iran") == []


def test_diff_only_compares_shared_hosts(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    _seed_scan(store, "iran", {"a": "INTERNATIONAL"})
    _seed_scan(store, "iran", {"b": "UNREACHABLE"})  # different host set
    assert alerts.diff_last_two(store, "iran") == []


# ---- alerts.process_scan_alerts (webhook) ---------------------------------
def test_process_alerts_disabled_is_noop(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.db")
    _seed_scan(store, "iran", {"a": "INTERNATIONAL"})
    _seed_scan(store, "iran", {"a": "UNREACHABLE"})

    def _boom(*a, **k):
        raise AssertionError("webhook must not fire when alerting disabled")

    monkeypatch.setattr(alerts, "_post_webhook", _boom)
    out = alerts.process_scan_alerts(store, "iran", Settings(alert_on_change=False))
    assert out == []


def test_process_alerts_posts_webhook_when_configured(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.db")
    _seed_scan(store, "iran", {"a": "INTERNATIONAL"})
    _seed_scan(store, "iran", {"a": "UNREACHABLE"})

    posted = {}

    def _capture(url, payload, **kw):
        posted["url"] = url
        posted["payload"] = payload

    monkeypatch.setattr(alerts, "_post_webhook", _capture)
    settings = Settings(alert_on_change=True, alert_webhook_url="http://example/hook")
    out = alerts.process_scan_alerts(store, "iran", settings, scan_id=99)
    assert len(out) == 1
    assert posted["url"] == "http://example/hook"
    assert posted["payload"]["scope"] == "iran"
    assert posted["payload"]["scan_id"] == 99
    assert posted["payload"]["changes"][0]["host"] == "a"


def test_process_alerts_logs_without_webhook(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.db")
    _seed_scan(store, "iran", {"a": "INTERNATIONAL"})
    _seed_scan(store, "iran", {"a": "UNREACHABLE"})

    def _boom(*a, **k):
        raise AssertionError("no webhook should fire when URL is blank")

    monkeypatch.setattr(alerts, "_post_webhook", _boom)
    out = alerts.process_scan_alerts(
        store, "iran", Settings(alert_on_change=True, alert_webhook_url="")
    )
    assert len(out) == 1  # change detected + logged, just no HTTP call


# ---- scheduler.ScanScheduler ---------------------------------------------
def _fake_scan_hosts(probe_fn):
    def _impl(hosts, *, count=4, timeout=2.0, concurrency=32, on_result=None):
        out = []
        for h in hosts:
            p = probe_fn(h)
            out.append(p)
            if on_result is not None:
                on_result(p)
        return out

    return _impl


def test_scheduler_run_once_persists_and_diffs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=10.0)),
    )
    monkeypatch.setattr(scanner, "check_abroad", lambda host, **kw: (None, 0, 0))
    monkeypatch.setattr(scanner, "_prepare_hosts", lambda scope, s: ["1.1.1.1"])

    store = HistoryStore(tmp_path / "h.db")
    sched = scheduler.ScanScheduler(
        "iran", 30, store=store, settings_provider=lambda: Settings(check_global=False)
    )
    state = sched.run_once()
    assert state.runs == 1
    assert state.last_scan_id is not None
    assert state.last_error is None
    assert len(store.list_scans()) == 1


def test_scheduler_run_is_failsoft(tmp_path, monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(scanner, "run_scan", _explode)
    store = HistoryStore(tmp_path / "h.db")
    sched = scheduler.ScanScheduler("iran", 30, store=store)
    state = sched.run_once()
    # Error captured, not raised; loop can continue.
    assert state.last_error is not None
    assert state.runs == 0


def test_scheduler_interval_floored():
    sched = scheduler.ScanScheduler("iran", 1)
    assert sched.interval >= 30.0
