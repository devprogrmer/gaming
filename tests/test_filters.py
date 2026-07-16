from __future__ import annotations

from gaming.models import Filters, IPRecord
from gaming.processing.filters import apply_filters, matches


def test_country_filter(sample_records):
    f = Filters(countries=["IR"])
    out = apply_filters(sample_records, f)
    assert [r.country for r in out] == ["IR"]


def test_asn_filter(sample_records):
    f = Filters(asns=["AS13335"])
    out = apply_filters(sample_records, f)
    assert len(out) == 1
    assert out[0].asn == "AS13335"


def test_provider_substring_filter(sample_records):
    f = Filters(providers=["cloud"])  # matches cloudflare
    out = apply_filters(sample_records, f)
    assert {r.provider for r in out} == {"cloudflare"}


def test_org_substring_filter(sample_records):
    f = Filters(organizations=["hetzner"])
    out = apply_filters(sample_records, f)
    assert len(out) == 1
    assert out[0].organization == "Hetzner Online"


def test_empty_filter_passes_all(sample_records):
    out = apply_filters(sample_records, Filters())
    assert len(out) == len(sample_records)


def test_iran_datacenter_focus(sample_records):
    f = Filters(iran_datacenter=True)
    out = apply_filters(sample_records, f)
    assert all(r.country == "IR" for r in out)
    assert len(out) == 1


def test_foreign_datacenter_focus(sample_records):
    f = Filters(foreign_datacenter=True)
    out = apply_filters(sample_records, f)
    assert all(r.country != "IR" for r in out)
    assert len(out) == 2


def test_combined_filters_are_and(sample_records):
    f = Filters(countries=["US"], providers=["cloudflare"])
    out = apply_filters(sample_records, f)
    assert len(out) == 1

    f2 = Filters(countries=["US"], providers=["hetzner"])
    assert apply_filters(sample_records, f2) == []


def test_matches_single():
    rec = IPRecord(prefix="1.2.3.0/24", country="DE", asn="AS24940")
    assert matches(rec, Filters(countries=["DE"]))
    assert not matches(rec, Filters(countries=["IR"]))
