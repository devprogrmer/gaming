from __future__ import annotations

import pytest

from gaming.interactive.classify import BAD, GOOD, MEDIUM, ProbeResult
from gaming.interactive.storage import HistoryStore


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history.db")


def _sample_results():
    return [
        (ProbeResult("1.1.1.1", sent=4, received=4, avg_ms=20.0), GOOD),
        (ProbeResult("2.2.2.2", sent=4, received=4, avg_ms=150.0), MEDIUM),
        (ProbeResult("3.3.3.3", sent=4, received=0), BAD),
    ]


def test_initialize_is_idempotent(store):
    store.initialize()
    store.initialize()  # must not raise
    assert store.list_scans() == []


def test_save_and_list_scan(store):
    scan_id = store.save_scan("iran", _sample_results())
    assert scan_id > 0

    scans = store.list_scans()
    assert len(scans) == 1
    s = scans[0]
    assert s.scope == "iran"
    assert s.total == 3
    assert s.good == 1
    assert s.medium == 1
    assert s.bad == 1


def test_get_results_ordered_best_first(store):
    scan_id = store.save_scan("foreign", _sample_results())
    rows = store.get_results(scan_id)
    assert [r.verdict for r in rows] == [GOOD, MEDIUM, BAD]
    assert rows[0].host == "1.1.1.1"
    assert rows[2].received == 0


def test_list_scans_newest_first(store):
    first = store.save_scan("iran", _sample_results())
    second = store.save_scan("foreign", _sample_results())
    scans = store.list_scans()
    assert [s.id for s in scans] == [second, first]


def test_persist_survives_new_store_instance(tmp_path):
    path = tmp_path / "history.db"
    HistoryStore(path).save_scan("iran", _sample_results())
    # A fresh store object pointed at the same file sees the data (persistence).
    reopened = HistoryStore(path)
    assert len(reopened.list_scans()) == 1


def test_clear(store):
    store.save_scan("iran", _sample_results())
    store.clear()
    assert store.list_scans() == []
