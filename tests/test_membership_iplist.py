"""Reverse IP membership lookup and the bare ip-list output format."""

from __future__ import annotations

import pytest

from gaming.interactive import membership
from gaming.interactive import ranges as ranges_mod
from gaming.models import IPRecord
from gaming.reporting import export, to_ip_list


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


@pytest.fixture
def stored():
    """A deliberately overlapping set: /16 containing a /24, plus IPv6."""
    ranges_mod.add_custom_range("iran_datacenter", "5.22.0.0/16", country="IR",
                                provider="Fooberg Hosting Ltd")
    ranges_mod.add_custom_range("iran_datacenter", "5.22.7.0/24", country="IR",
                                provider="Fooberg Tehran POP")
    ranges_mod.add_custom_range("foreign_cdn", "104.16.0.0/13", country="US",
                                provider="Cloudflare")
    ranges_mod.add_custom_range("foreign_datacenter", "2a01:4f8::/32", country="DE",
                                provider="Hetzner Online GmbH")


# ---- matching ------------------------------------------------------------
def test_single_match_reports_full_metadata(stored):
    result = membership.lookup_ip("104.16.5.9")
    assert result.found
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.cidr == "104.16.0.0/13"
    assert match.group == "foreign_cdn"
    assert match.country == "US"
    assert match.provider == "Cloudflare"


def test_overlapping_ranges_all_reported_most_specific_first(stored):
    """A /24 inside a /16 is two true answers; the /24 is the useful one."""
    result = membership.lookup_ip("5.22.7.42")
    assert [m.cidr for m in result.matches] == ["5.22.7.0/24", "5.22.0.0/16"]
    assert result.matches[0].provider == "Fooberg Tehran POP"


def test_address_in_the_outer_range_only_matches_once(stored):
    result = membership.lookup_ip("5.22.99.1")
    assert [m.cidr for m in result.matches] == ["5.22.0.0/16"]


def test_no_match_is_reported_clearly(stored):
    result = membership.lookup_ip("203.0.113.7")
    assert not result.found
    assert result.matches == []
    assert result.live is None
    assert result.as_dict()["found"] is False


def test_ipv6_membership(stored):
    result = membership.lookup_ip("2a01:4f8:c010:1234::1")
    assert result.found
    assert result.matches[0].cidr == "2a01:4f8::/32"
    assert result.matches[0].provider == "Hetzner Online GmbH"


def test_ipv4_address_never_matches_an_ipv6_range(stored):
    """Version confusion would produce nonsense answers."""
    assert not membership.lookup_ip("42.42.42.42").found


def test_invalid_address_raises(stored):
    with pytest.raises(ValueError):
        membership.lookup_ip("not-an-ip")
    with pytest.raises(ValueError):
        membership.lookup_ip("999.1.1.1")


def test_surrounding_whitespace_is_tolerated(stored):
    assert membership.lookup_ip("  104.16.5.9  ").found


def test_bundled_ranges_are_searched_by_default():
    """A match must be possible before the user has discovered anything."""
    result = membership.lookup_ip("1.1.1.1")
    assert result.found
    assert result.matches[0].origin == "bundled"


def test_bundled_ranges_can_be_excluded():
    assert not membership.lookup_ip("1.1.1.1", include_bundled=False).found


def test_exhaustively_discovered_ranges_are_searchable():
    """The obscure hosts exhaustive mode finds must be reverse-lookup-able."""
    ranges_mod.persist_exhaustive_records(
        [
            IPRecord(
                prefix="91.99.0.0/24",
                source="exhaustive",
                asn="AS44244",
                organization="Fooberg Hosting Ltd",
                country="IR",
            )
        ]
    )
    result = membership.lookup_ip("91.99.0.55", include_bundled=False)
    assert result.found
    assert result.matches[0].origin == ranges_mod.EXHAUSTIVE_ORIGIN
    assert result.matches[0].provider == "Fooberg Hosting Ltd"


def test_unnamed_allocations_are_still_reverse_lookup_able():
    from gaming.discovery.exhaustive import UNNAMED_ORG

    ranges_mod.persist_exhaustive_records(
        [
            IPRecord(
                prefix="91.98.0.0/24",
                source="exhaustive",
                organization=UNNAMED_ORG,
                country="IR",
            )
        ]
    )
    result = membership.lookup_ip("91.98.0.1", include_bundled=False)
    assert result.found
    assert result.matches[0].provider == UNNAMED_ORG


# ---- live fallback -------------------------------------------------------
def test_live_lookup_parses_an_rdap_ip_object(monkeypatch):
    payload = {
        "handle": "203.0.113.0/24",
        "startAddress": "203.0.113.0",
        "endAddress": "203.0.113.255",
        "country": "NL",
        "entities": [
            {
                "vcardArray": [
                    "vcard",
                    [["version", {}, "text", "4.0"], ["fn", {}, "text", "Obscure BV"]],
                ]
            }
        ],
    }
    monkeypatch.setattr(
        "gaming.utils.http.get_json", lambda url, **kw: payload
    )
    live = membership.live_lookup("203.0.113.7")
    assert live is not None
    assert live["cidr"] == "203.0.113.0/24"
    assert live["organization"] == "Obscure BV"
    assert live["source"] == "rdap"


def test_live_lookup_returns_none_when_offline(monkeypatch):
    from gaming.utils.http import HTTPError

    def boom(url, **kw):
        raise HTTPError("no route to host", status=None)

    monkeypatch.setattr("gaming.utils.http.get_json", boom)
    assert membership.live_lookup("203.0.113.7") is None


def test_live_lookup_rejects_a_bad_address():
    assert membership.live_lookup("nope") is None


# ---- ip-list format ------------------------------------------------------
def test_ip_list_emits_bare_addresses_only():
    records = [IPRecord(prefix="192.0.2.0/30", source="t", organization="Acme",
                        asn="AS64500", country="IR")]
    text = to_ip_list(records)
    lines = text.splitlines()
    assert lines == ["192.0.2.1", "192.0.2.2"]
    # No metadata whatsoever leaks into the output.
    for token in ("Acme", "AS64500", "IR", "/30", ","):
        assert token not in text


def test_ip_list_has_no_header_or_separators():
    text = to_ip_list([IPRecord(prefix="192.0.2.0/30", source="t")])
    assert not text.startswith("#")
    assert "---" not in text
    assert all(line.count(".") == 3 for line in text.splitlines())


def test_ip_list_is_reachable_through_the_export_dispatcher():
    records = [IPRecord(prefix="192.0.2.0/30", source="t")]
    assert export(records, "ip-list") == to_ip_list(records)
    assert export(records, "ip_list") == to_ip_list(records)


def test_ip_list_writes_a_file_with_a_trailing_newline(tmp_path):
    from gaming.reporting import write_ip_list

    path = tmp_path / "ips.txt"
    write_ip_list([IPRecord(prefix="192.0.2.0/30", source="t")], path)
    body = path.read_text(encoding="utf-8")
    assert body.endswith("\n")
    assert body.splitlines() == ["192.0.2.1", "192.0.2.2"]


def test_ip_list_of_nothing_is_empty_not_an_error():
    assert to_ip_list([]) == ""


def test_ip_list_expansion_is_bounded():
    """A /8 must not try to materialize 16 million lines."""
    text = to_ip_list([IPRecord(prefix="10.0.0.0/8", source="t")], max_hosts=100)
    assert 0 < len(text.splitlines()) <= 100


def test_ip_list_skips_records_without_a_prefix():
    class Bare:
        prefix = ""

    assert to_ip_list([Bare()]) == ""
