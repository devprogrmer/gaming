from __future__ import annotations

from gaming.models import Filters, IPRecord
from gaming.processing.filters import (
    apply_filters,
    has_provider_metadata,
    is_datacenter,
    is_datacenter_only,
    is_foreign_cdn,
    is_iranian_cdn,
    matches,
    matches_region,
    partition_by_country,
    region_of,
)


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


# ---- category classification: datacenter vs foreign-CDN vs Iranian-CDN ----
def _rec(org, provider=None, country=None):
    return IPRecord(
        prefix="1.2.3.0/24",
        organization=org,
        provider=provider or org.lower(),
        country=country,
    )


def test_datacenter_only_excludes_major_cdns():
    # A real hosting/datacenter provider qualifies.
    assert is_datacenter_only(_rec("Hetzner Online Hosting", country="DE"))
    # Major CDN/cloud/edge/WAF providers are excluded from the datacenter path.
    for cdn in ("Cloudflare, Inc.", "Fastly", "Akamai Technologies",
                "Facebook / Meta Platforms", "Google LLC"):
        rec = _rec(cdn, country="US")
        assert is_foreign_cdn(rec), cdn
        assert not is_datacenter_only(rec), cdn


def test_foreign_cdn_matcher_includes_expected_providers():
    for cdn in ("Cloudflare", "Fastly", "Akamai", "Meta Platforms", "Amazon AWS"):
        assert is_foreign_cdn(_rec(cdn, country="US")), cdn
    # An ordinary datacenter is not a foreign CDN.
    assert not is_foreign_cdn(_rec("Hetzner Online", country="DE"))


def test_iranian_cdn_is_separate_from_datacenter_and_foreign():
    ir_cdn = _rec("ArvanCloud CDN", provider="arvancloud", country="IR")
    assert is_iranian_cdn(ir_cdn)
    # Iranian CDN must not be mistaken for a foreign CDN.
    assert not is_foreign_cdn(ir_cdn)
    # A foreign CDN is not an Iranian CDN.
    assert not is_iranian_cdn(_rec("Cloudflare", country="US"))
    # A plain Iranian datacenter (no CDN/edge signal) is not an Iranian CDN.
    assert not is_iranian_cdn(_rec("Pars Pardazesh", provider="pars", country="IR"))


def test_iranian_cdn_country_keyword_fallback():
    # Sparse metadata: IR country + a generic "edge" keyword is enough.
    rec = _rec("Some Iranian Edge Network", provider="", country="IR")
    assert is_iranian_cdn(rec)


def test_datacenter_vs_unclassified_metadata():
    # Has datacenter keyword in org text -> classified as a datacenter.
    dc = IPRecord(prefix="1.2.3.0/24", organization="Foo Hosting", country="DE")
    assert is_datacenter(dc)
    assert has_provider_metadata(dc)

    # Has org text but no datacenter keyword -> not a datacenter, but classifiable.
    isp = IPRecord(prefix="1.2.3.0/24", organization="Some ISP", country="IR")
    assert not is_datacenter(isp)
    assert has_provider_metadata(isp)

    # RIR-style record: country only, no org/provider -> cannot be classified.
    # is_datacenter is False, but that's "unknown", surfaced via has_provider_metadata.
    rir = IPRecord(prefix="212.1.0.0/16", country="IR", organization=None, provider=None)
    assert not is_datacenter(rir)
    assert not has_provider_metadata(rir)


def test_partition_by_country_uses_country_as_authoritative_signal():
    # Mixed-country fixture: a genuinely-IR record, an Iranian-org record whose
    # actual prefix is in DE (foreign PoP / misattributed), and a record with no
    # country at all (ambiguous).
    genuine_ir = _rec("Pars Pardazesh", provider="pars", country="IR")
    ir_org_de_prefix = _rec("ArvanCloud edge", provider="arvancloud", country="DE")
    no_country = IPRecord(prefix="212.0.0.0/16", organization=None, provider=None)

    part = partition_by_country(
        [genuine_ir, ir_org_de_prefix, no_country], "IR"
    )
    # Only the genuinely IR-located record is matched.
    assert part.matched == [genuine_ir]
    # The Iranian-org-but-foreign-prefix record and the country-less record are
    # both unverified — excluded from the Iran-only set, not silently dropped.
    assert ir_org_de_prefix in part.unverified
    assert no_country in part.unverified


def test_region_mapping_and_matching():
    assert region_of(_rec("x", country="IR")) == "middle_east"
    assert region_of(_rec("x", country="DE")) == "europe"
    assert region_of(_rec("x", country="JP")) == "asia"
    assert region_of(_rec("x", country="BR")) is None
    assert region_of(_rec("x")) is None


def test_matches_region_filters_cidrs():
    de = _rec("Hetzner", country="DE")
    ir = _rec("Pars", country="IR")
    assert matches_region(de, "europe")
    assert not matches_region(de, "middle_east")
    assert matches_region(ir, "middle_east")
    # "all" always matches; unmapped country only matches "all".
    assert matches_region(de, "all")
    assert matches_region(_rec("x", country="BR"), "all")
    assert not matches_region(_rec("x", country="BR"), "europe")
