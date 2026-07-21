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


# ---- schema migration for bidirectional / port columns -------------------
_OLD_SCHEMA = """
CREATE TABLE scans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT    NOT NULL,
    scope      TEXT    NOT NULL,
    total      INTEGER NOT NULL DEFAULT 0,
    good       INTEGER NOT NULL DEFAULT 0,
    medium     INTEGER NOT NULL DEFAULT 0,
    bad        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE results (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id  INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    host     TEXT    NOT NULL,
    verdict  TEXT    NOT NULL,
    avg_ms   REAL,
    loss_pct REAL,
    sent     INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0
);
"""


def _make_old_db(path):
    """Build a pre-migration database (old columns only) with one scan + row."""
    import sqlite3

    conn = sqlite3.connect(path)
    with conn:
        conn.executescript(_OLD_SCHEMA)
        cur = conn.execute(
            "INSERT INTO scans (started_at, scope, total, good, medium, bad) "
            "VALUES ('2026-01-01T00:00:00+00:00', 'iran', 1, 1, 0, 0)"
        )
        scan_id = cur.lastrowid
        conn.execute(
            "INSERT INTO results (scan_id, host, verdict, avg_ms, loss_pct, sent, received) "
            "VALUES (?, '5.6.7.8', 'GOOD', 12.0, 0.0, 4, 4)",
            (scan_id,),
        )
    conn.close()
    return scan_id


def test_migration_upgrades_old_db_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    scan_id = _make_old_db(path)

    store = HistoryStore(path)
    store.initialize()  # must add the new columns idempotently

    # Existing scan + row survive.
    scans = store.list_scans()
    assert len(scans) == 1 and scans[0].scope == "iran"
    rows = store.get_results(scan_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.host == "5.6.7.8"
    assert row.verdict == GOOD
    # New columns read back as "not checked", never a misleading False.
    assert row.abroad_reachable is None
    assert row.abroad_nodes_ok is None
    assert row.abroad_nodes_total is None
    assert row.combined_verdict is None
    assert row.open_ports == []
    assert row.abroad_status is None  # Part D column absent pre-migration

    # Idempotent: a second initialize() over the migrated DB is a no-op.
    store.initialize()
    assert len(store.get_results(scan_id)) == 1


def test_new_db_persists_combined_fields(store):
    from gaming.interactive.classify import INTERNATIONAL, CombinedResult

    results = [(ProbeResult("8.8.8.8", sent=4, received=4, avg_ms=10.0), GOOD)]
    combined = [
        CombinedResult(
            probe=results[0][0],
            abroad_reachable=True,
            abroad_nodes_ok=4,
            abroad_nodes_total=4,
            open_ports=[80, 443],
        )
    ]
    scan_id = store.save_scan("foreign", results, combined=combined)
    row = store.get_results(scan_id)[0]
    assert row.abroad_reachable is True
    assert row.abroad_nodes_ok == 4
    assert row.abroad_nodes_total == 4
    assert row.combined_verdict == INTERNATIONAL
    assert row.open_ports == [80, 443]


def test_persists_abroad_status_unavailable(store):
    from gaming.interactive.classify import CombinedResult

    results = [(ProbeResult("8.8.8.8", sent=4, received=4, avg_ms=10.0), GOOD)]
    combined = [
        CombinedResult(
            probe=results[0][0],
            abroad_reachable=None,
            abroad_status="unavailable",
        )
    ]
    scan_id = store.save_scan("foreign", results, combined=combined)
    row = store.get_results(scan_id)[0]
    # The provider-outage signal survives the round-trip, distinct from a plain
    # "not checked" (which would have abroad_status None).
    assert row.abroad_status == "unavailable"
    assert row.abroad_reachable is None
