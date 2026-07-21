from __future__ import annotations

from gaming.interactive.filters_shared import (
    format_bare_ips,
    matches_cidr_query,
    matches_first_octet,
    parse_first_octets,
    record_matches,
)
from gaming.models import IPRecord


def test_parse_and_first_octet_reexport_behaviour():
    assert parse_first_octets("1, 2 ,3") == [1, 2, 3]
    assert matches_first_octet("185.51.200.0/22", [185]) is True
    assert matches_first_octet("2a01:4f8::/29", [42]) is False


def test_matches_cidr_query_numeric_is_anchored():
    assert matches_cidr_query("85.9.0.0/16", "85") is True
    # A leading-octet query must not match a non-leading occurrence.
    assert matches_cidr_query("185.85.0.0/16", "85") is False
    assert matches_cidr_query("85.9.0.0/16", "") is True  # empty matches all


def test_matches_cidr_query_substring_fallback():
    assert matches_cidr_query("185.51.200.0/22", "51.200") is True
    assert matches_cidr_query("185.51.200.0/22", "999") is False


def test_record_matches_combines_filters():
    rec = IPRecord(
        prefix="85.9.0.0/16",
        country="IR",
        provider="arvancloud",
        organization="ArvanCloud",
        asn="AS205585",
    )
    assert record_matches(rec, query="85") is True
    assert record_matches(rec, provider="arvan") is True
    assert record_matches(rec, country="ir") is True
    assert record_matches(rec, asn="205585") is True
    # AND semantics: one failing filter rejects the record.
    assert record_matches(rec, query="85", country="US") is False


def test_format_bare_ips_dedups():
    assert format_bare_ips([" 1.1.1.1 ", "1.1.1.1", "2.2.2.2"]) == "1.1.1.1\n2.2.2.2"
