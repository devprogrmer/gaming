from __future__ import annotations

from gaming.models import IPRecord
from gaming.processing.normalize import collapse_prefixes, normalize_records


def test_dedup_merges_metadata():
    recs = [
        IPRecord(prefix="1.2.3.0/24", source="rdap", asn="AS1", country="IR"),
        IPRecord(
            prefix="1.2.3.0/24",
            source="whois",
            organization="Example Org",
            notes="second",
        ),
    ]
    out = normalize_records(recs)
    assert len(out) == 1
    merged = out[0]
    assert merged.asn == "AS1"
    assert merged.country == "IR"
    assert merged.organization == "Example Org"
    # sources combined and sorted
    assert merged.source == "rdap+whois"
    assert "second" in merged.notes


def test_sorting_is_deterministic():
    recs = [
        IPRecord(prefix="10.0.0.0/8"),
        IPRecord(prefix="1.0.0.0/8"),
        IPRecord(prefix="2001:db8::/32"),
    ]
    out = normalize_records(recs)
    # IPv4 sorted before IPv6, ascending by address
    assert out[0].prefix == "1.0.0.0/8"
    assert out[1].prefix == "10.0.0.0/8"
    assert out[2].is_ipv6


def test_collapse_contained_prefixes():
    recs = [
        IPRecord(prefix="192.0.2.0/25", asn="AS1", country="US"),
        IPRecord(prefix="192.0.2.128/25", asn="AS1", country="US"),
    ]
    out = collapse_prefixes(recs)
    prefixes = {r.prefix for r in out}
    assert prefixes == {"192.0.2.0/24"}


def test_collapse_keeps_distinct_groups():
    recs = [
        IPRecord(prefix="192.0.2.0/25", asn="AS1", country="US"),
        IPRecord(prefix="192.0.2.128/25", asn="AS2", country="US"),
    ]
    out = collapse_prefixes(recs)
    # different ASNs must not be collapsed together
    assert len(out) == 2
