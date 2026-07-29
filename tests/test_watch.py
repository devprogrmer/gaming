"""24/7 watch loop: iteration order, fail-soft stages, and interval parsing."""

from __future__ import annotations

import pytest

from gaming.interactive import watch as watch_mod
from gaming.interactive.settings import Settings
from gaming.interactive.storage import HistoryStore
from gaming.interactive.watch import WatchLoop, parse_interval
from gaming.models import IPRecord


class _FakeReport:
    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.scope = "iran"


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """Replace the three network-touching stages with recorders."""
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    calls: dict[str, list] = {"discover": [], "persist": [], "scan": [], "alerts": []}

    def fake_discover(country, **kwargs):
        calls["discover"].append(country)
        return [
            IPRecord(prefix="5.22.0.0/21", source="exhaustive", organization="Fooberg")
        ]

    def fake_persist(records, **kwargs):
        calls["persist"].append(len(list(records)))
        return {"iran_datacenter": ["5.22.0.0/21"]}

    def fake_run_scan(scope, settings, **kwargs):
        calls["scan"].append(scope)
        return _FakeReport()

    def fake_scan_persist(report, store=None):
        return 42

    def fake_alerts(store, scope, settings, *, scan_id=None):
        calls["alerts"].append(scan_id)
        return []

    monkeypatch.setattr(
        "gaming.discovery.exhaustive.discover_country", fake_discover
    )
    monkeypatch.setattr(
        watch_mod.ranges_mod, "persist_exhaustive_prefixes", fake_persist
    )
    monkeypatch.setattr(watch_mod.scanner, "run_scan", fake_run_scan)
    monkeypatch.setattr(watch_mod.scanner, "persist", fake_scan_persist)
    monkeypatch.setattr(watch_mod.alerts, "process_scan_alerts", fake_alerts)
    return calls


def _loop(**kwargs) -> WatchLoop:
    kwargs.setdefault("settings_provider", Settings)
    # A real in-memory store, not a dummy: the persist stage now writes the
    # newly-discovered ranges to the "what's new" ledger.
    kwargs.setdefault("store", HistoryStore(":memory:"))
    loop = WatchLoop(country="IR", interval_seconds=600, **kwargs)
    # Bypass the (deliberate) production floor so multi-iteration tests do not
    # actually sleep between ticks.
    loop.interval = 0.01
    return loop


# ---- a healthy iteration -------------------------------------------------
def test_iteration_runs_discover_then_persist_then_scan(stub):
    state = _loop().run_once()
    assert stub["discover"] == ["IR"]
    assert stub["persist"] == [1]
    assert stub["scan"] == ["iran"]
    assert state.iterations == 1
    assert state.last_discovered == 1
    assert state.last_persisted == {"iran_datacenter": 1}
    assert state.last_scan_id == 42
    assert state.last_scanned == 3
    assert state.healthy


def test_multiple_iterations_accumulate(stub):
    loop = _loop()
    loop.run_forever(max_iterations=3)
    assert loop.state.iterations == 3
    assert stub["discover"] == ["IR", "IR", "IR"]
    assert stub["scan"] == ["iran"] * 3


def test_newly_discovered_ranges_are_persisted_before_the_scan(stub):
    """Ordering is the point: a range found this tick is scanned this tick."""
    order: list[str] = []
    monkey_persist = stub["persist"]

    loop = _loop()
    original_persist = loop._persist
    original_scan = loop._scan
    loop._persist = lambda st, recs: (order.append("persist"), original_persist(st, recs))[1]
    loop._scan = lambda st, s: (order.append("scan"), original_scan(st, s))[1]
    loop.run_once()
    assert order == ["persist", "scan"]
    assert monkey_persist == [1]


def test_state_serializes_for_the_dashboard(stub):
    state = _loop().run_once()
    payload = state.as_dict()
    assert payload["country"] == "IR"
    assert payload["healthy"] is True
    assert payload["last_scan_id"] == 42
    assert isinstance(payload["last_persisted"], dict)


# ---- fail-soft: one bad stage must not stop the watcher -----------------
def test_discovery_failure_still_lets_the_scan_run(stub, monkeypatch):
    def boom(country, **kwargs):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr("gaming.discovery.exhaustive.discover_country", boom)
    state = _loop().run_once()

    assert stub["scan"] == ["iran"]  # previously stored ranges still scanned
    assert not state.healthy
    assert any("discover" in e for e in state.errors)
    assert state.iterations == 1


def test_persist_failure_still_lets_the_scan_run(stub, monkeypatch):
    def boom(records, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(watch_mod.ranges_mod, "persist_exhaustive_prefixes", boom)
    state = _loop().run_once()
    assert stub["scan"] == ["iran"]
    assert any("persist" in e for e in state.errors)


def test_scan_failure_leaves_discovery_persisted(stub, monkeypatch):
    def boom(scope, settings, **kwargs):
        raise RuntimeError("probe socket denied")

    monkeypatch.setattr(watch_mod.scanner, "run_scan", boom)
    state = _loop().run_once()
    assert stub["persist"] == [1]  # the ranges were still saved
    assert any("scan" in e for e in state.errors)


def test_alerting_failure_does_not_fail_the_iteration(stub, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("webhook unreachable")

    monkeypatch.setattr(watch_mod.alerts, "process_scan_alerts", boom)
    state = _loop().run_once()
    assert state.last_scan_id == 42
    assert any("alerts" in e for e in state.errors)


def test_broken_settings_fall_back_to_defaults(stub):
    def boom():
        raise ValueError("settings.json is a directory")

    state = _loop(settings_provider=boom).run_once()
    assert stub["scan"] == ["iran"]
    assert any("settings" in e for e in state.errors)


def test_a_failed_iteration_does_not_stop_the_loop(stub, monkeypatch):
    """The essential property of an unattended watcher."""
    attempts = {"n": 0}

    def flaky(country, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return []

    monkeypatch.setattr("gaming.discovery.exhaustive.discover_country", flaky)
    loop = _loop()
    loop.run_forever(max_iterations=3)
    assert loop.state.iterations == 3
    assert attempts["n"] == 3


def test_errors_are_cleared_on_a_subsequent_good_iteration(stub, monkeypatch):
    calls = {"n": 0}

    def flaky(country, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return []

    monkeypatch.setattr("gaming.discovery.exhaustive.discover_country", flaky)
    loop = _loop()
    loop.run_once()
    assert not loop.state.healthy
    loop.run_once()
    assert loop.state.healthy


# ---- threading + lifecycle ----------------------------------------------
def test_stop_interrupts_the_idle_sleep_promptly(stub):
    """A stop during the long idle wait must not block for that whole wait."""
    loop = _loop()
    loop.interval = 30.0
    loop.start()
    loop.stop(timeout=5.0)
    assert not loop.running


def test_start_is_idempotent(stub):
    loop = _loop()
    loop.interval = 30.0
    loop.start()
    first = loop._thread
    loop.start()
    assert loop._thread is first
    loop.stop()


def test_iteration_callback_failure_is_swallowed(stub):
    def boom(state):
        raise RuntimeError("ui exploded")

    state = _loop(on_iteration=boom).run_once()
    assert state.iterations == 1


# ---- interval parsing ----------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1h", 3600.0),
        ("30m", 1800.0),
        ("2d", 172800.0),
        ("45s", 45.0),
        ("900", 900.0),
        (900, 900.0),
        ("  2H ", 7200.0),
    ],
)
def test_interval_parsing(raw, expected):
    assert parse_interval(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "nonsense", "h", "--"])
def test_bad_interval_falls_back_to_the_default(raw):
    assert parse_interval(raw) == watch_mod.DEFAULT_INTERVAL_SECONDS


def test_interval_is_floored(stub):
    """A one-second sweep interval would hammer the registries."""
    loop = WatchLoop(country="IR", interval_seconds=1)
    assert loop.interval == watch_mod.MIN_INTERVAL_SECONDS
