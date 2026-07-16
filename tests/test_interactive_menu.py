from __future__ import annotations

import io

import pytest

from gaming.interactive import scanner
from gaming.interactive.classify import ProbeResult
from gaming.interactive.menu import Menu
from gaming.interactive.storage import HistoryStore


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


def _run_menu(script: str, store: HistoryStore) -> str:
    stdin = io.StringIO(script)
    stdout = io.StringIO()
    menu = Menu(stdin=stdin, stdout=stdout, store=store)
    menu.run()
    return stdout.getvalue()


def test_menu_exit_immediately(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("0\n", store)
    assert "IP Health Scanner" in out
    assert "Goodbye." in out


def test_menu_eof_exits_cleanly(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("", store)  # immediate EOF
    assert "Goodbye." in out


def test_menu_scan_iran_persists(tmp_path, monkeypatch):
    # Deterministic, fast "network": every host is GOOD.
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )
    store = HistoryStore(tmp_path / "h.db")
    # 1) scan Iran, then 0) exit.
    out = _run_menu("1\n0\n", store)
    assert "Iran scan" in out or "Scanning" in out
    assert "GOOD" in out
    assert "Saved as scan #" in out
    # A scan was persisted.
    assert len(store.list_scans()) == 1


def test_menu_add_custom_range_flow(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 5) manage -> 3) add -> iran -> CIDR -> 0) back -> 0) exit
    script = "5\n3\niran\n203.0.113.0/24\n0\n0\n"
    out = _run_menu(script, store)
    assert "Added 203.0.113.0/24" in out

    from gaming.interactive import ranges

    assert "203.0.113.0/24" in ranges.custom_ranges("iran")


def test_menu_history_view(tmp_path, monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )
    store = HistoryStore(tmp_path / "h.db")
    # scan, then open history, view scan #1, then exit.
    out = _run_menu("1\n4\n1\n0\n", store)
    assert "WHEN (UTC)" in out  # history table header
    assert "HOST" in out  # detail table header


def test_menu_settings_edit_and_save(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 6) settings -> 5) probes per host -> 6 -> s) save -> 0) back -> 0) exit
    script = "6\n5\n6\ns\n0\n0\n"
    out = _run_menu(script, store)
    assert "Settings saved." in out

    from gaming.interactive.settings import load_settings

    assert load_settings().ping_count == 6


def _fake_scan_hosts(probe_fn):
    def _impl(hosts, *, count=4, timeout=2.0, concurrency=32, on_result=None):
        results = []
        for h in hosts:
            p = probe_fn(h)
            results.append(p)
            if on_result is not None:
                on_result(p)
        return results

    return _impl
