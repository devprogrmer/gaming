"""Tests for the discovery sources' live-lookup and parsing logic.

All tests are fully offline: the HTTP/socket boundary is monkeypatched, so no
real network calls are made. They exercise the real parsing code paths and the
graceful fallbacks (no seeds, request failure -> bundled sample data).
"""

from __future__ import annotations

import pytest

from gaming.discovery import build_source
from gaming.discovery.base import DiscoveryContext
from gaming.discovery.peeringdb import PeeringDBSource
from gaming.discovery.rdap import RDAPSource, _vcard_field
from gaming.discovery.whois import WhoisSource
from gaming.models import Filters


def _ctx(**filter_kwargs) -> DiscoveryContext:
    return DiscoveryContext(filters=Filters(**filter_kwargs), timeout=1.0, offline=False)


# --------------------------------------------------------------------------
# RDAP
# --------------------------------------------------------------------------
def _autnum_payload(name: str, country: str, registrant: str) -> dict:
    return {
        "name": name,
        "country": country,
        "entities": [
            {
                "roles": ["registrant"],
                "vcardArray": ["vcard", [["fn", {}, "text", registrant]]],
            }
        ],
    }


def test_rdap_enriches_prefixes_with_autnum(monkeypatch):
    src = RDAPSource(_ctx(asns=["AS13335"]))

    def fake_get_json(url, **kw):
        if "autnum" in url:
            return _autnum_payload("AS13335", "us", "Cloudflare, Inc.")
        return {
            "data": {"prefixes": [{"prefix": "104.16.0.0/13"}, {"prefix": "1.1.1.0/24"}]}
        }

    monkeypatch.setattr("gaming.discovery.rdap.get_json", fake_get_json)
    records = src._discover_online()

    assert {r.prefix for r in records} == {"104.16.0.0/13", "1.1.1.0/24"}
    assert all(r.asn == "AS13335" for r in records)
    # registrant vCard "fn" wins over the object name
    assert all(r.organization == "Cloudflare, Inc." for r in records)
    assert all(r.country == "US" for r in records)
    assert all(r.source == "rdap" for r in records)


def test_rdap_no_seeds_returns_empty_then_samples():
    src = RDAPSource(_ctx())
    # No seeds -> online path yields nothing...
    assert src._discover_online() == []
    # ...and the public discover() falls back to sample data.
    samples = src.discover()
    assert samples and all(s.source == "rdap" for s in samples)


def test_rdap_autnum_failure_still_yields_prefixes(monkeypatch):
    from gaming.utils.http import HTTPError

    src = RDAPSource(_ctx(asns=["AS64500"]))

    def fake_get_json(url, **kw):
        if "autnum" in url:
            raise HTTPError("boom")
        return {"data": {"prefixes": [{"prefix": "203.0.113.0/24"}]}}

    monkeypatch.setattr("gaming.discovery.rdap.get_json", fake_get_json)
    records = src._discover_online()
    assert [r.prefix for r in records] == ["203.0.113.0/24"]
    # org/country unknown because autnum failed, but prefix still emitted
    assert records[0].organization is None
    assert records[0].country is None


def test_rdap_discover_falls_back_on_total_failure(monkeypatch):
    from gaming.utils.http import HTTPError

    src = RDAPSource(_ctx(asns=["AS64500"]))

    def boom(url, **kw):
        raise HTTPError("network down")

    monkeypatch.setattr("gaming.discovery.rdap.get_json", boom)
    # autnum fails (-> None,None) and prefixes fail (-> []), so online is empty;
    # discover() then returns sample data.
    records = src.discover()
    assert records and all(r.notes.startswith("RDAP sample") for r in records)


def test_vcard_field_parsing():
    vcard = ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "ACME Corp"]]]
    assert _vcard_field(vcard, "fn") == "ACME Corp"
    assert _vcard_field(vcard, "email") is None
    assert _vcard_field(None, "fn") is None
    assert _vcard_field(["vcard"], "fn") is None


# --------------------------------------------------------------------------
# WHOIS
# --------------------------------------------------------------------------
_WHOIS_RESPONSE = """\
route:      185.51.200.0/22
descr:      Example Datacenter
origin:     AS58224

route6:     2a01:4f8::/29
descr:      Example IPv6 block
origin:     AS58224

% an informational comment block with no route
"""


def test_whois_parses_route_objects(monkeypatch):
    src = WhoisSource(_ctx(asns=["AS58224"]))
    monkeypatch.setattr(src, "_raw_query", lambda q: _WHOIS_RESPONSE)

    records = src._discover_online()
    prefixes = {r.prefix for r in records}
    assert prefixes == {"185.51.200.0/22", "2a01:4f8::/29"}
    assert all(r.asn == "AS58224" for r in records)
    assert any(r.organization == "Example Datacenter" for r in records)
    assert all(r.source == "whois" for r in records)


def test_whois_inverse_query_format(monkeypatch):
    captured = {}
    src = WhoisSource(_ctx(asns=["58224"]))

    def fake_raw(query):
        captured["query"] = query
        return "route: 185.51.200.0/22\n"

    monkeypatch.setattr(src, "_raw_query", fake_raw)
    src._discover_online()
    assert captured["query"] == "-i origin AS58224"


def test_whois_socket_error_skips_seed(monkeypatch):
    src = WhoisSource(_ctx(asns=["AS58224"]))

    def boom(query):
        raise OSError("connection refused")

    monkeypatch.setattr(src, "_raw_query", boom)
    # Online yields nothing; discover() falls back to samples.
    assert src._discover_online() == []
    assert src.discover()  # sample data


def test_whois_no_seeds():
    assert WhoisSource(_ctx())._discover_online() == []


# --------------------------------------------------------------------------
# PeeringDB
# --------------------------------------------------------------------------
def test_peeringdb_emits_peering_ips(monkeypatch):
    src = PeeringDBSource(_ctx(asns=["AS205585"]))

    def fake_get_json(url, **kw):
        if "/api/net?" in url:
            return {"data": [{"name": "ArvanCloud"}]}
        return {
            "data": [
                {"ipaddr4": "185.1.1.5", "ipaddr6": "2001:db8::5"},
                {"ipaddr4": "185.1.2.9", "ipaddr6": None},
            ]
        }

    monkeypatch.setattr("gaming.discovery.peeringdb.get_json", fake_get_json)
    records = src._discover_online()

    assert {r.prefix for r in records} == {
        "185.1.1.5/32",
        "2001:db8::5/128",
        "185.1.2.9/32",
    }
    assert all(r.organization == "ArvanCloud" for r in records)
    assert all(r.asn == "AS205585" for r in records)


def test_peeringdb_no_seeds():
    assert PeeringDBSource(_ctx())._discover_online() == []


def test_peeringdb_failure_falls_back(monkeypatch):
    from gaming.utils.http import HTTPError

    src = PeeringDBSource(_ctx(asns=["AS205585"]))
    monkeypatch.setattr(
        "gaming.discovery.peeringdb.get_json",
        lambda url, **kw: (_ for _ in ()).throw(HTTPError("down")),
    )
    assert src._discover_online() == []
    assert src.discover()  # sample fallback


# --------------------------------------------------------------------------
# Cross-source: offline mode never touches the network
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["rdap", "whois", "peeringdb"])
def test_offline_mode_uses_samples(name):
    ctx = DiscoveryContext(filters=Filters(asns=["AS13335"]), timeout=1.0, offline=True)
    src = build_source(name, ctx)
    records = src.discover()
    assert records and all(r.source == name for r in records)
