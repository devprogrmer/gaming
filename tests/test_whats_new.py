"""The watcher's "what's new since you last checked" ledger.

Covers the three things that were missing: a durable record of which ranges are
actually new (as opposed to a count), per-surface last-visited tracking, and a
plain report when there is nothing new.
"""

from __future__ import annotations

import io
import json

import pytest

from gaming.interactive import ranges as ranges_mod
from gaming.interactive import watch as watch_mod
from gaming.interactive import whats_new as whats_new_mod
from gaming.interactive.settings import Settings
from gaming.interactive.storage import HistoryStore
from gaming.interactive.watch import WatchLoop
from gaming.models import IPRecord


class _FakeReport:
    total = 0
    scope = "iran"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(home):
    s = HistoryStore(home / "h.db")
    s.initialize()
    return s


# ---- the persistence layer now says *which* ranges are new ---------------
def test_persist_returns_the_prefixes_it_inserted(home):
    records = [
        IPRecord(prefix="185.1.1.0/24", source="x", country="IR", organization="A"),
        IPRecord(prefix="185.2.2.0/24", source="x", country="IR", organization="B"),
    ]
    first = ranges_mod.persist_exhaustive_prefixes(records, home_country="IR")
    assert sorted(first["iran_datacenter"]) == ["185.1.1.0/24", "185.2.2.0/24"]

    grown = records + [
        IPRecord(prefix="185.3.3.0/24", source="x", country="IR", organization="C")
    ]
    second = ranges_mod.persist_exhaustive_prefixes(grown, home_country="IR")
    assert second["iran_datacenter"] == ["185.3.3.0/24"]


def test_the_count_returning_function_still_works(home):
    """Backward compatibility: existing callers keep their counts."""
    records = [IPRecord(prefix="185.4.4.0/24", source="x", country="IR")]
    assert ranges_mod.persist_exhaustive_records(records) == {"iran_datacenter": 1}
    assert ranges_mod.persist_exhaustive_records(records) == {}


def test_save_discovered_still_returns_a_count(home):
    added = ranges_mod.save_discovered("iran_datacenter", ["185.5.5.0/24"])
    assert added == 1
    assert ranges_mod.save_discovered("iran_datacenter", ["185.5.5.0/24"]) == 0


# ---- the ledger ----------------------------------------------------------
def test_recording_the_same_prefix_twice_keeps_the_first_sighting(store):
    store.record_discoveries([{"prefix": "185.6.6.0/24", "first_seen": "2026-01-01T00:00:00+00:00"}])
    again = store.record_discoveries(
        [{"prefix": "185.6.6.0/24", "first_seen": "2026-06-01T00:00:00+00:00"}]
    )
    assert again == []
    rows = store.discoveries_after(0)
    assert len(rows) == 1
    assert rows[0].first_seen == "2026-01-01T00:00:00+00:00"


def test_discoveries_after_filters_by_watermark(store):
    store.record_discoveries([{"prefix": "10.0.0.0/24"}, {"prefix": "10.0.1.0/24"}])
    rows = store.discoveries_after(0)
    assert [r.prefix for r in rows] == ["10.0.0.0/24", "10.0.1.0/24"]
    assert [r.prefix for r in store.discoveries_after(rows[0].id)] == ["10.0.1.0/24"]
    assert store.discoveries_after(rows[-1].id) == []


def test_a_discovery_in_the_same_second_as_the_visit_is_not_swallowed(store):
    """Why the watermark is a row id and not a timestamp.

    ``first_seen`` has second resolution, so a sweep landing in the same second
    as an acknowledgement would be permanently invisible under a ``>`` time
    comparison.
    """
    same_second = "2026-01-01T00:00:00+00:00"
    store.record_discoveries([{"prefix": "12.0.0.0/24", "first_seen": same_second}])
    result = whats_new_mod.whats_new(store, whats_new_mod.MENU)
    whats_new_mod.acknowledge(
        store, whats_new_mod.MENU, up_to_id=result.up_to_id,
    )
    store.record_discoveries([{"prefix": "12.0.1.0/24", "first_seen": same_second}])

    unread = whats_new_mod.whats_new(store, whats_new_mod.MENU)
    assert [r.prefix for r in unread.rows] == ["12.0.1.0/24"]


def test_the_ledger_survives_a_reopened_database(home):
    first = HistoryStore(home / "h.db")
    first.initialize()
    first.record_discoveries([{"prefix": "10.9.9.0/24"}])

    reopened = HistoryStore(home / "h.db")
    assert [r.prefix for r in reopened.discoveries_after(0)] == ["10.9.9.0/24"]


def test_an_old_database_without_the_new_tables_is_upgraded(home):
    """Backward compatibility with on-disk data written before this feature."""
    import sqlite3

    path = home / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE scans (id INTEGER PRIMARY KEY, started_at TEXT, scope TEXT,"
        " total INTEGER, good INTEGER, medium INTEGER, bad INTEGER);"
        "CREATE TABLE results (id INTEGER PRIMARY KEY, scan_id INTEGER, host TEXT,"
        " verdict TEXT, avg_ms REAL, loss_pct REAL, sent INTEGER, received INTEGER);"
    )
    conn.commit()
    conn.close()

    store = HistoryStore(path)
    store.initialize()
    store.record_discoveries([{"prefix": "10.8.8.0/24"}])
    assert [r.prefix for r in store.discoveries_after(0)] == ["10.8.8.0/24"]


# ---- per-surface last-visited -------------------------------------------
def test_each_surface_tracks_its_own_last_visit(store):
    store.record_discoveries([{"prefix": "10.1.0.0/24"}])

    assert whats_new_mod.whats_new(store, whats_new_mod.MENU).count == 1
    assert whats_new_mod.whats_new(store, whats_new_mod.WEB).count == 1

    whats_new_mod.acknowledge(store, whats_new_mod.WEB)

    # Acknowledging the web surface must not clear the menu's notice.
    assert whats_new_mod.whats_new(store, whats_new_mod.WEB).count == 0
    assert whats_new_mod.whats_new(store, whats_new_mod.MENU).count == 1


def test_reading_does_not_acknowledge(store):
    store.record_discoveries([{"prefix": "10.2.0.0/24"}])
    whats_new_mod.whats_new(store, whats_new_mod.MENU)
    assert whats_new_mod.whats_new(store, whats_new_mod.MENU).count == 1


def test_a_range_discovered_after_the_visit_is_new_again(store):
    store.record_discoveries([{"prefix": "10.3.0.0/24", "first_seen": "2026-01-01T00:00:00+00:00"}])
    whats_new_mod.acknowledge(store, whats_new_mod.MENU)
    store.record_discoveries([{"prefix": "10.3.1.0/24", "first_seen": "2099-01-01T00:00:00+00:00"}])

    result = whats_new_mod.whats_new(store, whats_new_mod.MENU)
    assert [r.prefix for r in result.rows] == ["10.3.1.0/24"]


def test_nothing_new_is_reported_plainly(store):
    store.record_discoveries([{"prefix": "10.4.0.0/24"}])
    whats_new_mod.acknowledge(store, whats_new_mod.MENU)

    result = whats_new_mod.whats_new(store, whats_new_mod.MENU)
    assert not result.has_new
    assert "Nothing new" in result.summary()
    assert "Nothing new" in whats_new_mod.render(result, io.StringIO())


def test_an_empty_ledger_on_a_first_visit_says_so(store):
    result = whats_new_mod.whats_new(store, whats_new_mod.MENU)
    assert result.first_visit
    assert "No ranges discovered yet" in result.summary()


# ---- the watch loop feeds the ledger ------------------------------------
@pytest.fixture
def loop_env(home, monkeypatch):
    """A watch loop whose sweep returns a caller-controlled record list."""
    box: dict[str, list] = {"records": []}

    monkeypatch.setattr(
        "gaming.discovery.exhaustive.discover_country",
        lambda country, **kw: list(box["records"]),
    )
    monkeypatch.setattr(
        watch_mod.scanner, "run_scan", lambda scope, settings, **kw: _FakeReport()
    )
    monkeypatch.setattr(watch_mod.scanner, "persist", lambda report, store=None: 1)
    monkeypatch.setattr(
        watch_mod.alerts,
        "process_scan_alerts",
        lambda store, scope, settings, *, scan_id=None: [],
    )

    store = HistoryStore(home / "h.db")
    store.initialize()
    loop = WatchLoop(
        country="IR", interval_seconds=600, store=store, settings_provider=Settings
    )
    return loop, store, box


def test_a_cycle_mixing_known_and_new_ranges_records_only_the_new_ones(loop_env):
    loop, store, box = loop_env
    box["records"] = [
        IPRecord(prefix="45.1.1.0/24", source="x", country="IR", organization="Known"),
    ]
    loop.run_once()
    assert [r.prefix for r in store.discoveries_after(0)] == ["45.1.1.0/24"]

    box["records"] = [
        IPRecord(prefix="45.1.1.0/24", source="x", country="IR", organization="Known"),
        IPRecord(
            prefix="45.2.2.0/24",
            source="x",
            country="IR",
            organization="Fresh",
            asn="AS64500",
        ),
    ]
    state = loop.run_once()

    assert state.last_discovered == 2
    assert state.last_new_prefixes == ["45.2.2.0/24"]
    rows = {r.prefix: r for r in store.discoveries_after(0)}
    assert set(rows) == {"45.1.1.0/24", "45.2.2.0/24"}
    assert rows["45.2.2.0/24"].org == "Fresh"
    assert rows["45.2.2.0/24"].asn == "AS64500"
    assert rows["45.2.2.0/24"].country == "IR"


def test_a_repeated_sweep_adds_nothing_new(loop_env):
    loop, store, box = loop_env
    box["records"] = [IPRecord(prefix="45.3.3.0/24", source="x", country="IR")]
    loop.run_once()
    whats_new_mod.acknowledge(store, whats_new_mod.MENU)

    state = loop.run_once()
    assert state.last_new_prefixes == []
    assert not whats_new_mod.whats_new(store, whats_new_mod.MENU).has_new


def test_a_broken_ledger_does_not_stop_the_iteration(loop_env, monkeypatch):
    """Fail-soft: the ranges are stored and scannable regardless."""
    loop, store, box = loop_env
    box["records"] = [IPRecord(prefix="45.4.4.0/24", source="x", country="IR")]

    def boom(rows):
        raise OSError("database is locked")

    monkeypatch.setattr(store, "record_discoveries", boom)
    state = loop.run_once()

    assert state.iterations == 1
    assert state.last_scan_id == 1
    assert any("ledger" in e for e in state.errors)
    assert "45.4.4.0/24" in ranges_mod.load_category("iran_datacenter")


# ---- menu surface --------------------------------------------------------
def _menu_context(store, inputs=()):
    from gaming.interactive.actions.context import ActionContext

    out = io.StringIO()
    pending = list(inputs)
    return out, ActionContext(
        settings=Settings(),
        store=store,
        stdout=out,
        print_=lambda text="": out.write(text + "\n"),
        prompt=lambda _m: pending.pop(0) if pending else "",
        choose=lambda _t, _o: None,
    )


def test_menu_option_shows_and_then_acknowledges(store):
    from gaming.interactive.actions import whats_new

    store.record_discoveries([{"prefix": "77.1.1.0/24", "org": "Fooberg"}])
    out, ctx = _menu_context(store)
    whats_new(ctx)

    text = out.getvalue()
    assert "77.1.1.0/24" in text
    assert "Fooberg" in text
    assert not whats_new_mod.whats_new(store, whats_new_mod.MENU).has_new


def test_menu_banner_appears_only_when_there_is_something_new(store):
    from gaming.interactive.actions import whats_new_notice

    assert whats_new_notice(store) == ""
    store.record_discoveries([{"prefix": "77.2.2.0/24"}])
    # First visit: "new" means the whole ledger, so it is worded as such.
    assert "1 discovered range on record" in whats_new_notice(store)

    whats_new_mod.acknowledge(store, whats_new_mod.MENU)
    assert whats_new_notice(store) == ""

    store.record_discoveries(
        [{"prefix": "77.3.3.0/24", "first_seen": "2099-01-01T00:00:00+00:00"}]
    )
    assert "1 new range discovered since" in whats_new_notice(store)


def test_menu_banner_survives_an_unreadable_ledger():
    """A broken store must not stop the menu from opening."""
    from gaming.interactive.actions import whats_new_notice

    class _Broken:
        def last_visited(self, surface):
            raise OSError("no such file")

    assert whats_new_notice(_Broken()) == ""


def test_menu_option_is_listed():
    from gaming.interactive.menu import _MENU_OPTIONS

    labels = {key: label for key, label in _MENU_OPTIONS}
    assert "11" in labels
    assert "new" in labels["11"].lower()


# ---- CLI surface --------------------------------------------------------
def test_cli_whats_new_prints_and_acknowledges(home, capsys):
    from gaming.cli import main
    from gaming.interactive import paths

    store = HistoryStore(paths.database_path())
    store.initialize()
    store.record_discoveries([{"prefix": "88.1.1.0/24", "org": "Barcorp"}])

    assert main(["watch", "--whats-new"]) == 0
    out = capsys.readouterr().out
    assert "88.1.1.0/24" in out
    assert "Barcorp" in out

    assert main(["watch", "--whats-new"]) == 0
    assert "Nothing new" in capsys.readouterr().out


# ---- web surface --------------------------------------------------------
@pytest.fixture
def web_env(home):
    from gaming.web.auth import CredentialStore, RateLimiter
    from gaming.web.handlers import WebApp

    store = HistoryStore(home / "h.db")
    store.initialize()
    creds = CredentialStore(home / "creds.json")
    _c, password = creds.ensure_credentials()
    app = WebApp(
        credentials=creds, store=store, rate_limiter=RateLimiter(max_attempts=3)
    )
    return app, creds, password, store


def test_web_endpoint_reports_new_entries(web_env):
    from tests.test_web_server import _login, _req

    app, creds, pw, store = web_env
    cookie = _login(app, creds, pw)
    store.record_discoveries([{"prefix": "99.1.1.0/24", "org": "Bazco"}])

    resp = app.handle(_req("GET", "/api/whats-new", cookie=cookie))
    body = json.loads(resp.body)
    assert body["has_new"] is True
    assert body["count"] == 1
    assert body["rows"][0]["prefix"] == "99.1.1.0/24"
    assert body["rows"][0]["org"] == "Bazco"


def test_web_ack_clears_only_the_web_notice(web_env):
    from tests.test_web_server import _login, _req

    app, creds, pw, store = web_env
    cookie = _login(app, creds, pw)
    store.record_discoveries([{"prefix": "99.2.2.0/24"}])

    ack = app.handle(_req("POST", "/api/whats-new/ack", body={}, cookie=cookie))
    assert json.loads(ack.body)["acknowledged"] is True

    resp = app.handle(_req("GET", "/api/whats-new", cookie=cookie))
    assert json.loads(resp.body)["has_new"] is False
    # The terminal still has its own unread notice.
    assert whats_new_mod.whats_new(store, whats_new_mod.MENU).has_new


def test_web_endpoint_says_so_when_nothing_is_new(web_env):
    from tests.test_web_server import _login, _req

    app, creds, pw, _store = web_env
    cookie = _login(app, creds, pw)

    body = json.loads(app.handle(_req("GET", "/api/whats-new", cookie=cookie)).body)
    assert body["has_new"] is False
    assert body["rows"] == []
    assert "No ranges discovered yet" in body["summary"]


def test_web_endpoints_require_authentication(web_env):
    from tests.test_web_server import _req

    app, _c, _p, _s = web_env
    assert app.handle(_req("GET", "/api/whats-new")).status == 401
    assert app.handle(_req("POST", "/api/whats-new/ack", body={})).status == 401
