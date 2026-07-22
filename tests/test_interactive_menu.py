from __future__ import annotations

import io

import pytest

from gaming.interactive import scanner
from gaming.interactive.classify import ProbeResult
from gaming.interactive.menu import (
    Menu,
    format_bare_ips,
    matches_first_octet,
    parse_first_octets,
)
from gaming.interactive.storage import HistoryStore


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


@pytest.fixture(autouse=True)
def _no_real_global(monkeypatch):
    """Keep menu scans offline: stub the abroad (check-host.net) pass.

    ``run_scan`` runs an abroad pass by default (Settings.check_global). Stub it
    to "not checked" so menu tests never touch the network; tests that exercise
    bidirectional output override this stub explicitly.
    """
    from gaming.interactive import scanner as _scanner

    monkeypatch.setattr(
        _scanner, "check_abroad", lambda host, **kw: (None, 0, 0)
    )
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


def test_menu_scan_saved_persists(tmp_path, monkeypatch):
    # Deterministic, fast "network": every host is GOOD.
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )
    from gaming.interactive import ranges

    # Seed a saved category so there is something to scan.
    ranges.save_discovered(
        "iran_datacenter", ["185.51.200.0/22"],
        metadata={"185.51.200.0/22": ("IR", "pars")},
    )
    store = HistoryStore(tmp_path / "h.db")
    # 1) scan saved -> origin Iran(1) -> class Datacenter(1) -> 0) exit.
    out = _run_menu("1\n1\n1\n0\n", store)
    assert "Scanning" in out
    assert "Saved as scan #" in out
    assert "Alive IPs (copy-paste ready)" in out
    assert len(store.list_scans()) == 1


def test_menu_scan_saved_empty_is_friendly(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # No saved foreign_cdn ranges -> friendly message, no crash.
    out = _run_menu("1\n2\n2\n0\n", store)
    assert "No saved CIDRs for that selection" in out


def test_menu_scan_shows_bidirectional_summary(tmp_path, monkeypatch):
    """A scan surfaces the abroad column + combined reachability summary line."""
    from gaming.interactive import ranges
    from gaming.interactive import scanner as scanner_mod

    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )
    # Abroad check succeeds -> combined verdict INTERNATIONAL.
    monkeypatch.setattr(
        scanner_mod, "check_abroad", lambda host, **kw: (True, 4, 4)
    )
    ranges.save_discovered(
        "iran_datacenter", ["185.51.200.0/24"],
        metadata={"185.51.200.0/24": ("IR", "pars")},
    )
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("1\n1\n1\n0\n", store)
    assert "INTERNATIONAL" in out  # combined summary line + WHITELIST column
    assert "ABROAD" in out  # results table gained the abroad column
    assert "Saved as scan #" in out


def test_menu_scan_whitelist_only_export(tmp_path, monkeypatch):
    """With export_international_only, the bare-IP block keeps only whitelisted IPs."""
    from gaming.interactive import ranges
    from gaming.interactive import scanner as scanner_mod
    from gaming.interactive.settings import Settings, save_settings

    # One host answers abroad (INTERNATIONAL), one does not (IRAN_ONLY).
    def _probe(h):
        return ProbeResult(h, sent=4, received=4, avg_ms=15.0)

    def _abroad(host, **kw):
        return (True, 4, 4) if host.endswith(".1") else (False, 0, 4)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(_probe))
    monkeypatch.setattr(scanner_mod, "check_abroad", _abroad)

    save_settings(Settings(export_international_only=True))
    # A /30 samples two usable hosts (.1 and .2) -> deterministic two-host scan.
    ranges.save_discovered(
        "iran_datacenter", ["185.9.9.0/30"],
        metadata={"185.9.9.0/30": ("IR", "pars")},
    )
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("1\n1\n1\n0\n", store)
    assert "Whitelisted (INTERNATIONAL) IPs" in out
    export_block = out.split("Whitelisted (INTERNATIONAL) IPs", 1)[-1]
    assert "185.9.9.1" in export_block  # abroad-OK host kept
    assert "185.9.9.2" not in export_block  # abroad-FAIL host excluded


def test_menu_scan_saved_iran_excludes_non_ir_located(tmp_path, monkeypatch):
    """Regression: an Iran-origin scan of saved ranges must only scan CIDRs
    verified as IR-located, not foreign CIDRs saved under an Iranian category.

    An Iranian CDN's overseas PoP (or a record whose registered ASN is Iranian
    but whose prefix geolocates abroad) gets saved into an Iran category with a
    non-IR country. The scan must drop it into a "location unverified" bucket
    instead of leaking it into the Iranian result set.
    """
    scanned: list[str] = []

    def _probe(host):
        scanned.append(host)
        return ProbeResult(host, sent=4, received=4, avg_ms=15.0)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(_probe))
    from gaming.interactive import ranges

    # Mixed saved data under the Iranian datacenter category:
    ranges.save_discovered(
        "iran_datacenter",
        ["185.51.200.0/24", "5.5.5.0/24", "9.9.9.0/24"],
        metadata={
            "185.51.200.0/24": ("IR", "pars"),      # genuinely Iranian
            "5.5.5.0/24": ("DE", "arvancloud"),      # Iranian org, foreign PoP
            "9.9.9.0/24": (None, "someprovider"),    # ambiguous, no country
        },
    )
    store = HistoryStore(tmp_path / "h.db")
    # 1) scan saved -> origin Iran(1) -> class Datacenter(1) -> exit
    out = _run_menu("1\n1\n1\n0\n", store)

    # The two non-IR-located CIDRs are surfaced as excluded, not silently dropped.
    assert "not verified as located in Iran" in out
    assert "5.5.5.0/24" in out
    assert "9.9.9.0/24" in out
    # Only the genuinely-Iranian CIDR was actually probed.
    assert any(h.startswith("185.51.200.") for h in scanned)
    assert not any(h.startswith("5.5.5.") for h in scanned)
    assert not any(h.startswith("9.9.9.") for h in scanned)


def test_menu_add_custom_range_flow(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 3) manage -> 4) add -> group iran -> CIDR -> 0) back -> 0) exit
    script = "3\n4\niran\n203.0.113.0/24\n0\n0\n"
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
    from gaming.interactive import ranges

    ranges.save_discovered(
        "iran_datacenter", ["185.51.200.0/22"],
        metadata={"185.51.200.0/22": ("IR", "pars")},
    )
    store = HistoryStore(tmp_path / "h.db")
    # scan saved (Iran/DC), then open history(4), view scan #1, then exit.
    out = _run_menu("1\n1\n1\n4\n1\n0\n", store)
    assert "WHEN (UTC)" in out  # history table header
    assert "HOST" in out  # detail table header


def test_menu_settings_edit_and_save(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 5) settings -> 5) probes per host -> 6 -> s) save -> 0) back -> 0) exit
    script = "5\n5\n6\ns\n0\n0\n"
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


# ---- first-octet filter helpers -----------------------------------------
def test_parse_first_octets_basic():
    assert parse_first_octets("1, 2 ,3") == [1, 2, 3]
    assert parse_first_octets("0,255") == [0, 255]


def test_parse_first_octets_dedups_and_skips_blanks():
    assert parse_first_octets("5,,5, 10, ") == [5, 10]


def test_parse_first_octets_empty_is_empty_list():
    assert parse_first_octets("") == []
    assert parse_first_octets("  ,  ") == []


def test_parse_first_octets_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse_first_octets("1,abc,3")


def test_parse_first_octets_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_first_octets("256")
    with pytest.raises(ValueError):
        parse_first_octets("-1")


def test_matches_first_octet_cidr_and_ip():
    assert matches_first_octet("185.51.200.0/22", [185]) is True
    assert matches_first_octet("185.51.200.0/22", [10, 185]) is True
    assert matches_first_octet("8.8.8.8", [8]) is True
    assert matches_first_octet("192.0.2.0/24", [185]) is False


def test_matches_first_octet_empty_allows_all():
    assert matches_first_octet("1.2.3.0/24", []) is True


def test_matches_first_octet_ipv6_never_matches():
    assert matches_first_octet("2a01:4f8::/29", [42]) is False


def test_matches_first_octet_malformed_is_false():
    assert matches_first_octet("not-a-cidr", [1]) is False


def test_menu_filter_by_first_octet_flow(tmp_path, monkeypatch):
    # Stub discovery/process so the flow is deterministic and offline.
    from gaming import pipeline
    from gaming.models import IPRecord

    records = [
        IPRecord(prefix="185.51.200.0/22", source="t", country="IR"),
        IPRecord(prefix="8.8.8.0/24", source="t", country="US"),
    ]
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )

    store = HistoryStore(tmp_path / "h.db")
    # 7) filter -> octets "185" -> 1) all datacenters -> blank country -> 0) exit
    out = _run_menu("7\n185\n1\n\n0\n", store)
    assert "first octet [185]" in out
    # The octet count is reported independently of datacenter classification.
    assert "1 of 2 discovered record(s) match first octet [185]" in out
    # Neither record has org/provider text, so it can't be classified — but the
    # matching CIDR must still be surfaced as "unclassified", not dropped to zero.
    assert "0 classified as datacenters" in out
    assert "1 unclassified" in out
    assert "185.51.200.0/22" in out


def test_menu_filter_by_first_octet_all_datacenters(tmp_path, monkeypatch):
    from gaming import pipeline
    from gaming.models import IPRecord

    records = [
        IPRecord(
            prefix="185.51.200.0/22",
            source="t",
            country="IR",
            organization="Arvan Cloud hosting",
        ),
        IPRecord(prefix="185.99.1.0/24", source="t", country="IR", organization="Some ISP"),
        IPRecord(prefix="8.8.8.0/24", source="t", country="US", organization="Google Cloud"),
    ]
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    # Every scanned host answers GOOD so the auto-scan phase has something to keep.
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )

    store = HistoryStore(tmp_path / "h.db")
    # octets "185" -> 1) all datacenters -> blank country
    out = _run_menu("7\n185\n1\n\n0\n", store)
    assert "classified as datacenters" in out
    # The datacenter/hosting/cloud org with first octet 185 is classified as such.
    assert "185.51.200.0/22" in out
    # "Some ISP" has org text but no datacenter keyword -> classified "not datacenter".
    assert "1 not datacenters" in out
    assert "8.8.8.0/24" not in out  # datacenter but wrong first octet
    # Auto-scan phase ran and kept the (located, GOOD) host.
    assert "strict GOOD only" in out
    assert "meet both strict reachability" in out
    assert "Saved as scan #" in out
    assert len(store.list_scans()) == 1


def test_menu_filter_octet_rir_records_no_org_surface_as_unclassified(
    tmp_path, monkeypatch
):
    """Regression: RIR-sourced records (country only, no org/provider) must not
    vanish behind the "all datacenters" filter.

    This reproduces the real-world report: discovering --country IR returns
    RIRSource records with organization=None and provider=None, and filtering by
    a matching first octet with "all datacenters" returned 0 every time. The
    octet-only match count must be surfaced, and the matching CIDRs shown in the
    "unclassified" bucket instead of being silently dropped.
    """
    from gaming import pipeline
    from gaming.models import IPRecord

    # Exactly what RIRSource._discover_online yields: org/provider both None.
    records = [
        IPRecord(prefix="212.1.0.0/16", source="rir", country="IR",
                 organization=None, provider=None, notes="RIR delegated allocation"),
        IPRecord(prefix="212.2.0.0/16", source="rir", country="IR",
                 organization=None, provider=None),
        IPRecord(prefix="85.9.0.0/16", source="rir", country="IR",
                 organization=None, provider=None),
    ]
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )

    store = HistoryStore(tmp_path / "h.db")
    # 7) filter -> octets "212" -> 1) all datacenters -> blank country -> exit
    out = _run_menu("7\n212\n1\n\n0\n", store)

    # The octet match is non-zero and reported independently of classification.
    assert "2 of 3 discovered record(s) match first octet [212]" in out
    # None can be classified (no org/provider) -> all land in the unclassified
    # bucket, NOT dropped to a misleading zero.
    assert "0 classified as datacenters" in out
    assert "2 unclassified" in out
    assert "212.1.0.0/16" in out
    assert "212.2.0.0/16" in out
    assert "85.9.0.0/16" not in out  # wrong octet
    # The unclassified CIDRs are still actionable: they reach the auto-scan.
    assert "Auto-scanning" in out


def test_menu_filter_by_first_octet_specific_provider(tmp_path, monkeypatch):
    from gaming import pipeline
    from gaming.models import Filters, IPRecord

    records = [
        IPRecord(prefix="185.51.200.0/22", source="t", country="IR",
                 organization="Arvan Cloud"),
        IPRecord(prefix="185.99.1.0/24", source="t", country="IR",
                 organization="Hetzner Online"),
    ]
    seen: dict = {}

    def _fake_process(recs, filters, **kwargs):
        # Record the filters so we can assert the provider term was threaded in,
        # and emulate the real provider-substring filtering.
        seen["filters"] = filters
        from gaming.processing.filters import apply_filters

        return apply_filters(recs, filters)

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", _fake_process)
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=15.0)),
    )

    store = HistoryStore(tmp_path / "h.db")
    # octets "185" -> 2) specific -> "arvan" -> blank country
    out = _run_menu("7\n185\n2\narvan\n\n0\n", store)
    assert isinstance(seen["filters"], Filters)
    assert seen["filters"].providers == ["arvan"]
    assert "provider/org ~ 'arvan'" in out
    assert "185.51.200.0/22" in out  # Arvan matched
    assert "185.99.1.0/24" not in out  # Hetzner filtered out by provider


def test_menu_filter_autoscan_strict_reachability(tmp_path, monkeypatch):
    """Only GOOD hosts survive the strict-reachability gate; MEDIUM/BAD drop."""
    from gaming import pipeline
    from gaming.models import IPRecord

    records = [
        IPRecord(prefix="185.1.1.0/24", source="t", country="IR", organization="A cloud"),
        IPRecord(prefix="185.2.2.0/24", source="t", country="IR", organization="B hosting"),
    ]
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))

    # 185.1.1.1 -> GOOD (fast, no loss); 185.2.2.1 -> BAD (unreachable).
    def _probe(host):
        if host == "185.1.1.1":
            return ProbeResult(host, sent=4, received=4, avg_ms=10.0)
        return ProbeResult(host, sent=4, received=0)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(_probe))

    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("7\n185\n1\n\n0\n", store)
    assert "185.1.1.1" in out  # GOOD host qualifies
    # The BAD host must not appear in the qualifying list.
    qualifying_section = out.split("meet both strict reachability", 1)[-1]
    assert "185.2.2.1" not in qualifying_section


def test_menu_filter_autoscan_location_requirement(tmp_path, monkeypatch):
    """Records without a known country are excluded before scanning."""
    from gaming import pipeline
    from gaming.models import IPRecord

    records = [
        IPRecord(prefix="185.1.1.0/24", source="t", country="IR", organization="A cloud"),
        IPRecord(prefix="185.2.2.0/24", source="t", organization="B hosting"),  # no country
    ]
    scanned_hosts: list[str] = []

    def _probe(host):
        scanned_hosts.append(host)
        return ProbeResult(host, sent=4, received=4, avg_ms=10.0)

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(_probe))

    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("7\n185\n1\n\n0\n", store)
    assert "location requirement" in out
    assert "Excluding 1 record(s) with no known location" in out
    # The country-less host was never probed.
    assert "185.2.2.1" not in scanned_hosts
    assert scanned_hosts == ["185.1.1.1"]


def test_menu_filter_by_first_octet_specific_blank_name(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 7) filter -> octets "10" -> 2) specific -> blank name -> exit
    out = _run_menu("7\n10\n2\n\n0\n", store)
    assert "No name provided" in out


def test_menu_filter_by_first_octet_bad_dc_choice(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 7) filter -> octets "10" -> 9 (invalid dc choice) -> exit
    out = _run_menu("7\n10\n9\n0\n", store)
    assert "Please choose 1 or 2" in out


def test_menu_filter_by_first_octet_invalid_input(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("7\n999\n0\n", store)
    assert "Invalid input" in out


# ---- clean bare-IP output ------------------------------------------------
def test_format_bare_ips_one_per_line_no_symbols():
    out = format_bare_ips(["1.1.1.1", "8.8.8.8"])
    assert out == "1.1.1.1\n8.8.8.8"
    # No prefixes, bullets, colours, or CIDR suffixes.
    assert "/" not in out
    assert "  " not in out
    assert "\x1b" not in out


def test_format_bare_ips_dedups_and_strips():
    assert format_bare_ips([" 1.1.1.1 ", "1.1.1.1", "", "2.2.2.2"]) == "1.1.1.1\n2.2.2.2"


def test_format_bare_ips_empty():
    assert format_bare_ips([]) == ""


# ---- menu exposes new scan/update options --------------------------------
def test_menu_lists_new_scan_and_update_options(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("0\n", store)
    assert "Scan saved ranges" in out
    assert "Discover & save provider ranges" in out
    assert "Manage IP ranges" in out
    assert "Update installed version" in out


# ---- menu option 9: launch web panel --------------------------------------
def test_menu_launch_web_panel_starts_server_and_returns_to_menu(tmp_path, monkeypatch):
    """Selecting 'Launch web panel' starts the same in-process server logic as
    `gaming web`, prints the startup banner, and returns control to the menu
    loop once the (faked, immediately-returning) server stops.
    """
    from gaming.web import server as web_server

    started = {"served": False, "closed": False}

    class _FakeHTTPD:
        def __init__(self, addr, handler):
            self.socket = object()
            self.address = addr

        def serve_forever(self, poll_interval=0.5):
            started["served"] = True

        def shutdown(self):
            pass

        def server_close(self):
            started["closed"] = True

    monkeypatch.setattr(web_server, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(web_server, "_pick_free_port", lambda bind: 0)
    monkeypatch.setattr(web_server, "_detect_server_ip", lambda: "203.0.113.5")

    store = HistoryStore(tmp_path / "h.db")
    # 9) launch web panel -> blank bind/port/tls (defaults) -> 0) exit menu.
    out = _run_menu("9\n\n\n\n0\n", store)

    assert started["served"] and started["closed"]
    assert "gaming web dashboard" in out
    assert "Press Ctrl+C to stop." in out
    # The menu loop resumed and exited normally afterward.
    assert "Goodbye." in out


def test_menu_banner_renders(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("0\n", store)
    # The devprogrmer wordmark banner appears at the top (ASCII art, SSH-safe).
    assert "devprogrmer" in out


def _patch_scan(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=12.0)),
    )


def test_menu_discover_saves_all_categories(tmp_path, monkeypatch):
    """Discover & save persists CIDRs across every category, from seed data."""
    from gaming import pipeline
    from gaming.interactive import ranges

    # Keep the live pipeline offline/deterministic — bundled seed data alone
    # must aggregate many providers across all four categories.
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))

    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("2\n0\n", store)
    assert "Saved" in out and "into Manage IP Ranges" in out
    # Every category gained entries (not just one provider).
    for category in ranges.CATEGORIES:
        assert len(ranges.load_category(category)) > 0, category
    # Iranian CDN aggregates MORE than one provider CIDR.
    assert len(ranges.load_category("iran_cdn")) > 1


def test_menu_discovered_ranges_persist_across_instances(tmp_path, monkeypatch):
    """Discovered CIDRs survive after the menu exits (not RAM-only)."""
    from gaming import pipeline
    from gaming.interactive import ranges

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))

    store = HistoryStore(tmp_path / "h.db")
    _run_menu("2\n0\n", store)  # discover & save, then exit
    before = {c: ranges.load_category(c) for c in ranges.CATEGORIES}

    # A brand-new Menu instance (fresh process simulation) still sees them via
    # Manage IP ranges -> list by category.
    out = _run_menu("3\n1\n0\n0\n", store)
    assert "iran_cdn" in out
    assert any(cidr in out for cidr in before["iran_cdn"])


def test_menu_scan_class_selection_loads_right_category(tmp_path, monkeypatch):
    """Selecting Foreign + CDN scans only foreign_cdn saved CIDRs."""
    _patch_scan(monkeypatch)
    from gaming.interactive import ranges

    ranges.save_discovered(
        "foreign_cdn", ["1.1.1.0/24"], metadata={"1.1.1.0/24": ("US", "cloudflare")}
    )
    ranges.save_discovered(
        "foreign_datacenter",
        ["5.5.5.0/24"],
        metadata={"5.5.5.0/24": ("DE", "hetzner")},
    )
    store = HistoryStore(tmp_path / "h.db")
    # 1) scan saved -> origin Foreign(2) -> class CDN(2) -> exit
    out = _run_menu("1\n2\n2\n0\n", store)
    assert "foreign_cdn" in out
    assert "1.1.1.1" in out  # the CDN host was scanned
    # The datacenter CIDR must NOT leak into a CDN-class scan.
    assert "5.5.5.1" not in out


def test_menu_scan_reports_lowest_latency_country(tmp_path, monkeypatch):
    """A foreign scan reports which country answers fastest from here."""
    from gaming.interactive import ranges

    ranges.save_discovered(
        "foreign_datacenter",
        ["5.5.5.0/24"],
        metadata={"5.5.5.0/24": ("DE", "hetzner")},
    )
    ranges.save_discovered(
        "foreign_datacenter",
        ["9.9.9.0/24"],
        metadata={"9.9.9.0/24": ("NL", "leaseweb")},
    )

    # DE answers fast (20ms), NL slow (200ms) -> DE should be reported best.
    def _probe(host):
        if host.startswith("5.5.5"):
            return ProbeResult(host, sent=4, received=4, avg_ms=20.0)
        return ProbeResult(host, sent=4, received=4, avg_ms=200.0)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(_probe))
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("1\n2\n1\n0\n", store)
    assert "Latency by destination country" in out
    assert "Lowest latency from here: DE" in out


def test_menu_manage_add_remove_by_category(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # manage(3) -> add(4) -> group iran_cdn -> CIDR -> back(0) -> exit(0)
    out = _run_menu("3\n4\niran_cdn\n203.0.113.0/24\n0\n0\n", store)
    assert "Added 203.0.113.0/24 to iran_cdn" in out
    from gaming.interactive import ranges

    assert "203.0.113.0/24" in ranges.custom_ranges("iran_cdn")


# ---- provider-picker discover -> save -> scan flow (menu option 8) --------
def test_menu_discover_save_scan_lists_iran_providers(tmp_path, monkeypatch):
    """Picking Iran shows the real known Iranian providers as a numbered list."""
    from gaming import pipeline

    # Keep it offline: no live records, seed CIDRs only.
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    _patch_scan(monkeypatch)

    store = HistoryStore(tmp_path / "h.db")
    # 8) discover+scan -> origin Iran(1) -> provider "All"(1) -> exit(0)
    out = _run_menu("8\n1\n1\n0\n", store)
    # The picker names actual Iranian providers from providers.toml.
    assert "ArvanCloud CDN" in out
    assert "Pars Pardazesh Datacenter" in out
    assert "All providers" in out


def test_menu_discover_save_scan_lists_foreign_providers(tmp_path, monkeypatch):
    from gaming import pipeline

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    _patch_scan(monkeypatch)

    store = HistoryStore(tmp_path / "h.db")
    # 8) -> origin Foreign(2) -> provider "All"(1) -> exit(0)
    out = _run_menu("8\n2\n1\n0\n", store)
    assert "Hetzner Online Hosting" in out
    assert "Cloudflare" in out


def test_menu_discover_save_scan_single_provider_pipeline(tmp_path, monkeypatch):
    """One specific provider: discover -> auto-save -> scan, one continuous flow."""
    from gaming import pipeline
    from gaming.interactive import providers, ranges

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    _patch_scan(monkeypatch)

    # ArvanCloud CDN is the first Iranian provider -> option 2 (after "All").
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("8\n1\n2\n0\n", store)

    assert "ArvanCloud CDN" in out
    # The seed CIDRs were auto-persisted into iran_cdn without a manual step.
    arvan = providers.providers_for_origin("iran")[0]
    saved = ranges.load_category("iran_cdn")
    assert any(cidr in saved for cidr in arvan.cidrs)
    # The flow proceeded straight to a scan and saved it.
    assert "Scanning" in out
    assert "Saved as scan #" in out
    assert len(store.list_scans()) == 1


def test_menu_discover_save_scan_persists_and_scans_live_records(tmp_path, monkeypatch):
    """Live-discovered CIDRs are saved into the right category AND scanned."""
    from gaming import pipeline
    from gaming.interactive import ranges
    from gaming.models import IPRecord

    # Simulate the Part-1 fix producing a real live prefix for the picked ASN.
    live = [
        IPRecord(
            prefix="185.143.240.0/24",
            source="asn_bgp",
            asn="AS205585",
            organization="ArvanCloud",
            country="IR",
            provider="arvancloud cdn",
        )
    ]
    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(live))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))

    scanned: list[str] = []

    def _probe(host):
        scanned.append(host)
        return ProbeResult(host, sent=4, received=4, avg_ms=12.0)

    monkeypatch.setattr(scanner, "scan_hosts", _fake_scan_hosts(_probe))

    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("8\n1\n2\n0\n", store)  # Iran -> ArvanCloud CDN

    # The live CIDR landed in iran_cdn and its hosts were probed.
    assert "185.143.240.0/24" in ranges.load_category("iran_cdn")
    assert any(h.startswith("185.143.240.") for h in scanned)
    assert "Saved as scan #" in out


def test_menu_discover_save_scan_seeds_pipeline_with_asns(tmp_path, monkeypatch):
    """The live pipeline is seeded with the provider's ASNs (Part-1 root fix)."""
    from gaming import pipeline

    seen: dict = {}

    def _fake_discover(config, filters=None, **kwargs):
        seen["filters"] = filters
        seen["timeout"] = kwargs.get("timeout")
        seen["verbose_errors"] = kwargs.get("verbose_errors")
        return []

    monkeypatch.setattr(pipeline, "discover", _fake_discover)
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    _patch_scan(monkeypatch)

    store = HistoryStore(tmp_path / "h.db")
    _run_menu("8\n1\n2\n0\n", store)  # Iran -> ArvanCloud CDN

    # Without seed ASNs every source returns [] and discovery silently falls
    # back to sample data — so the flow MUST pass the provider's ASN through.
    assert "AS205585" in seen["filters"].asns
    assert seen["timeout"] == 15.0  # longer bulk timeout, not the 5s default
    assert seen["verbose_errors"] is True
