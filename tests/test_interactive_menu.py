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

    store = HistoryStore(tmp_path / "h.db")
    # 8) filter -> octets "185" -> 1) all datacenters -> blank country -> 0) exit
    out = _run_menu("8\n185\n1\n\n0\n", store)
    assert "first octet [185]" in out
    # Neither record has datacenter-ish org text, so "all datacenters" yields none.
    assert "(none)" in out


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
    out = _run_menu("8\n185\n1\n\n0\n", store)
    assert "all datacenters" in out
    # Only the datacenter/hosting/cloud org with first octet 185 survives.
    assert "185.51.200.0/22" in out
    assert "185.99.1.0/24" not in out  # first octet matches but not a datacenter
    assert "8.8.8.0/24" not in out  # datacenter but wrong first octet
    # Auto-scan phase ran and kept the (located, GOOD) host.
    assert "strict GOOD only" in out
    assert "meet both strict reachability" in out
    assert "Saved as scan #" in out
    assert len(store.list_scans()) == 1


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
    out = _run_menu("8\n185\n2\narvan\n\n0\n", store)
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
    out = _run_menu("8\n185\n1\n\n0\n", store)
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
    out = _run_menu("8\n185\n1\n\n0\n", store)
    assert "location requirement" in out
    assert "Excluding 1 record(s) with no known location" in out
    # The country-less host was never probed.
    assert "185.2.2.1" not in scanned_hosts
    assert scanned_hosts == ["185.1.1.1"]


def test_menu_filter_by_first_octet_specific_blank_name(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 8) filter -> octets "10" -> 2) specific -> blank name -> exit
    out = _run_menu("8\n10\n2\n\n0\n", store)
    assert "No name provided" in out


def test_menu_filter_by_first_octet_bad_dc_choice(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    # 8) filter -> octets "10" -> 9 (invalid dc choice) -> exit
    out = _run_menu("8\n10\n9\n0\n", store)
    assert "Please choose 1 or 2" in out


def test_menu_filter_by_first_octet_invalid_input(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("8\n999\n0\n", store)
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
    assert "Scan Datacenters" in out
    assert "Scan Foreign CDN/Cloud Providers" in out
    assert "Scan Iranian CDN Providers" in out
    assert "Update installed version" in out


def _category_records():
    from gaming.models import IPRecord

    return [
        IPRecord(prefix="1.1.1.0/24", source="t", country="US",
                 organization="Cloudflare, Inc.", provider="cloudflare"),
        IPRecord(prefix="5.5.5.0/24", source="t", country="DE",
                 organization="Hetzner Online Hosting", provider="hetzner"),
        IPRecord(prefix="185.51.200.0/22", source="t", country="IR",
                 organization="ArvanCloud CDN", provider="arvancloud"),
    ]


def _patch_pipeline(monkeypatch, records):
    from gaming import pipeline

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: list(records))
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))
    monkeypatch.setattr(
        scanner,
        "scan_hosts",
        _fake_scan_hosts(lambda h: ProbeResult(h, sent=4, received=4, avg_ms=12.0)),
    )


def test_menu_datacenter_scan_excludes_cdns(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch, _category_records())
    store = HistoryStore(tmp_path / "h.db")
    # 9) Scan Datacenters -> region 4 (All) -> exit
    out = _run_menu("9\n4\n0\n", store)
    assert "[Datacenter]" in out
    # Hetzner (real DC) is in; Cloudflare and ArvanCloud CDN are excluded.
    assert "5.5.5.0/24" in out
    assert "1.1.1.0/24" not in out
    assert "185.51.200.0/22" not in out
    assert "Alive IPs (copy-paste ready)" in out


def test_menu_foreign_cdn_scan_includes_cdns(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch, _category_records())
    store = HistoryStore(tmp_path / "h.db")
    # 10) Scan Foreign CDN/Cloud -> region 4 (All) -> exit
    out = _run_menu("10\n4\n0\n", store)
    assert "[Foreign CDN/Cloud]" in out
    assert "1.1.1.0/24" in out  # Cloudflare included
    assert "5.5.5.0/24" not in out  # Hetzner (plain DC) excluded


def test_menu_iranian_cdn_scan_is_separate(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch, _category_records())
    store = HistoryStore(tmp_path / "h.db")
    # 11) Scan Iranian CDN -> region 1 (Middle East) -> exit
    out = _run_menu("11\n1\n0\n", store)
    assert "[Iranian CDN]" in out
    assert "185.51.200.0/22" in out  # ArvanCloud CDN included
    assert "1.1.1.0/24" not in out  # foreign CDN excluded
    assert "5.5.5.0/24" not in out  # foreign DC excluded


def test_menu_region_selection_filters_cidrs(tmp_path, monkeypatch):
    from gaming.models import IPRecord

    records = [
        IPRecord(prefix="5.5.5.0/24", source="t", country="DE",
                 organization="Hetzner Hosting", provider="hetzner"),
        IPRecord(prefix="45.45.45.0/24", source="t", country="SG",
                 organization="Acme Hosting", provider="acme"),
    ]
    _patch_pipeline(monkeypatch, records)
    store = HistoryStore(tmp_path / "h.db")
    # 9) Datacenter -> region 2 (Europe): only the DE record should match.
    out = _run_menu("9\n2\n0\n", store)
    assert "5.5.5.0/24" in out
    assert "45.45.45.0/24" not in out


def test_menu_iranian_cdn_no_match_friendly(tmp_path, monkeypatch):
    from gaming.models import IPRecord

    # Only foreign records — Iranian CDN scan should find nothing.
    records = [
        IPRecord(prefix="1.1.1.0/24", source="t", country="US",
                 organization="Cloudflare", provider="cloudflare"),
    ]
    _patch_pipeline(monkeypatch, records)
    store = HistoryStore(tmp_path / "h.db")
    out = _run_menu("11\n1\n0\n", store)
    assert "(none)" in out
