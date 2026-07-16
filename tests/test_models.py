from __future__ import annotations

import pytest

from gaming.models import (
    Filters,
    IPRecord,
    normalize_asn,
    normalize_prefix,
)


def test_normalize_asn_forms():
    assert normalize_asn("13335") == "AS13335"
    assert normalize_asn("AS13335") == "AS13335"
    assert normalize_asn("as13335") == "AS13335"
    assert normalize_asn(" as 0013335 ".replace(" ", "")) == "AS13335"


def test_normalize_prefix_from_bare_ip():
    assert normalize_prefix("8.8.8.8") == "8.8.8.8/32"
    assert normalize_prefix("2001:db8::1") == "2001:db8::1/128"


def test_normalize_prefix_cidr_host_bits_relaxed():
    # strict=False collapses host bits.
    assert normalize_prefix("185.143.232.5/22") == "185.143.232.0/22"


def test_normalize_prefix_invalid():
    with pytest.raises(ValueError):
        normalize_prefix("not-an-ip")
    with pytest.raises(ValueError):
        normalize_prefix("")


def test_iprecord_post_init_normalizes():
    rec = IPRecord(prefix="1.2.3.4", asn="as9", country="us")
    assert rec.prefix == "1.2.3.4/32"
    assert rec.asn == "AS9"
    assert rec.country == "US"


def test_iprecord_sample_host():
    single = IPRecord(prefix="8.8.8.8/32")
    assert single.sample_host() == "8.8.8.8"

    net = IPRecord(prefix="192.0.2.0/24")
    # first usable host
    assert net.sample_host() == "192.0.2.1"


def test_iprecord_to_dict_roundtrip_fields():
    rec = IPRecord(prefix="1.2.3.0/24", source="x", open_ports=[80, 443])
    d = rec.to_dict()
    assert d["prefix"] == "1.2.3.0/24"
    assert d["open_ports"] == [80, 443]
    for field in IPRecord.FIELDS:
        assert field in d


def test_filters_normalization_and_empty():
    f = Filters(countries=["ir", "de"], asns=["13335"], providers=["Cloudflare"])
    assert f.countries == ["IR", "DE"]
    assert f.asns == ["AS13335"]
    assert f.providers == ["cloudflare"]
    assert not f.is_empty()
    assert Filters().is_empty()
