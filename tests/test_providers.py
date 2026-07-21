from __future__ import annotations

import ipaddress

import pytest

from gaming.interactive import providers
from gaming.processing.filters import classify_category


def test_seed_records_load():
    recs = providers.load_seed_records()
    assert len(recs) > 30  # broad coverage, many providers
    for r in recs:
        ipaddress.ip_network(r.prefix, strict=False)  # every CIDR is valid


def test_seed_records_cover_all_categories():
    recs = providers.load_seed_records()
    cats = {classify_category(r) for r in recs}
    assert {"iran_datacenter", "iran_cdn", "foreign_datacenter", "foreign_cdn"} <= cats


def test_seed_classification_matches_declared_category():
    # Every seed record's declared category (stored in notes as 'seed:<cat>')
    # must agree with the runtime classifier — no silent misfiling.
    recs = providers.load_seed_records()
    for r in recs:
        declared = r.notes.replace("seed:", "")
        assert classify_category(r) == declared, (r.organization, declared)


def test_seed_aggregates_multiple_iranian_providers():
    recs = providers.load_seed_records()
    iranian = {
        r.organization
        for r in recs
        if classify_category(r) in ("iran_cdn", "iran_datacenter")
    }
    # Not just one Iranian provider — many.
    assert len(iranian) >= 5


def test_seed_includes_named_foreign_cdns():
    recs = providers.load_seed_records()
    orgs = " ".join((r.organization or "").lower() for r in recs)
    for name in ("cloudflare", "fastly", "akamai", "meta", "google"):
        assert name in orgs


# ---- provider listing API (backs the interactive picker) -----------------
def test_load_providers_returns_provider_objects():
    provs = providers.load_providers()
    assert provs
    arvan = next(p for p in provs if p.name == "ArvanCloud CDN")
    assert arvan.origin == "iran"
    assert arvan.category == "iran_cdn"
    assert arvan.country == "IR"
    assert arvan.asns == ["AS205585"]
    assert arvan.cidrs  # at least one seed CIDR


def test_providers_for_origin_splits_iran_and_foreign():
    iran = providers.providers_for_origin("iran")
    foreign = providers.providers_for_origin("foreign")
    iran_names = {p.name for p in iran}
    foreign_names = {p.name for p in foreign}

    assert all(p.origin == "iran" for p in iran)
    assert all(p.origin == "foreign" for p in foreign)
    # Known members land on the correct side.
    assert "Pars Pardazesh Datacenter" in iran_names
    assert "Hetzner Online Hosting" in foreign_names
    # The two origins never overlap.
    assert iran_names.isdisjoint(foreign_names)


def test_providers_for_origin_rejects_unknown():
    with pytest.raises(ValueError):
        providers.providers_for_origin("mars")


def test_provider_to_records_classify_into_declared_category():
    for prov in providers.load_providers():
        for rec in prov.to_records():
            assert classify_category(rec) == prov.category


# ---- seed refresh / staleness (Part C2) ----------------------------------
def test_refresh_flags_stale_cidr(monkeypatch):
    from gaming.models import IPRecord

    prov = providers.Provider(
        name="Example",
        category="foreign_datacenter",
        country="US",
        asns=["AS64500"],
        cidrs=["10.0.0.0/24", "203.0.113.0/24"],
    )
    monkeypatch.setattr(providers, "load_providers", lambda: [prov])

    # Live lookup only still announces the first block; the second is gone.
    def _fake_discover(self):
        return [IPRecord(prefix="10.0.0.0/16", source="asn_bgp")]

    from gaming.discovery.asn_bgp import ASNBGPSource

    monkeypatch.setattr(ASNBGPSource, "discover", _fake_discover)

    checks = providers.refresh_seed_data(timeout=1.0)
    assert len(checks) == 1
    c = checks[0]
    assert c.checked is True
    assert c.stale == ["203.0.113.0/24"]  # not covered -> flagged, not deleted


def test_refresh_no_asns_is_unchecked(monkeypatch):
    prov = providers.Provider(
        name="NoASN", category="foreign_datacenter", asns=[], cidrs=["1.2.3.0/24"]
    )
    monkeypatch.setattr(providers, "load_providers", lambda: [prov])
    checks = providers.refresh_seed_data()
    assert checks[0].checked is False
    assert checks[0].stale == []


def test_refresh_is_failsoft_on_lookup_error(monkeypatch):
    prov = providers.Provider(
        name="Boom", category="foreign_datacenter", asns=["AS1"], cidrs=["1.2.3.0/24"]
    )
    monkeypatch.setattr(providers, "load_providers", lambda: [prov])

    from gaming.discovery.asn_bgp import ASNBGPSource

    def _explode(self):
        raise RuntimeError("network down")

    monkeypatch.setattr(ASNBGPSource, "discover", _explode)
    checks = providers.refresh_seed_data()
    # Error swallowed; provider marked unchecked, no crash.
    assert checks[0].checked is False
    assert checks[0].stale == []


# ---- Part F: seed validation + [meta] last_validated marker ---------------
def test_seed_file_has_meta_marker():
    # The bundled seed file exposes a [meta].last_validated marker (possibly "").
    assert isinstance(providers.seed_last_validated(), str)


def _write_seed_copy(tmp_path, monkeypatch, body: str):
    """Point providers._seed_file_path at a temp copy so tests never touch the
    real bundled file, and make the readers use the same copy."""
    seed = tmp_path / "providers.toml"
    seed.write_text(body, encoding="utf-8")
    monkeypatch.setattr(providers, "_seed_file_path", lambda: seed)
    monkeypatch.setattr(
        providers, "_read_seed_toml", lambda: __import__("tomllib").loads(seed.read_text())
    )
    return seed


_SAMPLE_SEED = '''[meta]
last_validated = ""

[[provider]]
name = "Example DC"
category = "foreign_datacenter"
country = "US"
asns = ["AS64500"]
cidrs = ["203.0.113.0/24"]
'''


def test_update_last_validated_preserves_providers(tmp_path, monkeypatch):
    seed = _write_seed_copy(tmp_path, monkeypatch, _SAMPLE_SEED)
    ok = providers.update_last_validated("2026-07-21")
    assert ok is True
    text = seed.read_text()
    # Marker updated...
    assert 'last_validated = "2026-07-21"' in text
    # ...and every provider entry is byte-for-byte intact.
    assert '[[provider]]' in text
    assert 'name = "Example DC"' in text
    assert 'asns = ["AS64500"]' in text
    assert 'cidrs = ["203.0.113.0/24"]' in text
    # Re-parses cleanly and still yields the provider.
    import tomllib

    parsed = tomllib.loads(text)
    assert parsed["meta"]["last_validated"] == "2026-07-21"
    assert parsed["provider"][0]["name"] == "Example DC"


def test_update_last_validated_readonly_returns_false(monkeypatch):
    monkeypatch.setattr(providers, "_seed_file_path", lambda: None)
    assert providers.update_last_validated("2026-07-21") is False


def test_validate_seed_data_updates_marker_and_reports(tmp_path, monkeypatch):
    seed = _write_seed_copy(tmp_path, monkeypatch, _SAMPLE_SEED)
    from gaming.discovery.asn_bgp import ASNBGPSource
    from gaming.models import IPRecord

    # Announced prefixes still cover the seed CIDR -> not stale, provider checked.
    monkeypatch.setattr(
        ASNBGPSource,
        "discover",
        lambda self: [IPRecord(prefix="203.0.113.0/24", source="asn_bgp")],
    )
    report = providers.validate_seed_data(timeout=1.0)
    assert report.marker_updated is True
    assert report.checks[0].checked is True
    assert report.checks[0].stale == []
    # Provider entry survived the marker write.
    assert 'name = "Example DC"' in seed.read_text()
    # Marker now reflects the run.
    assert providers.seed_last_validated() == report.last_validated


def test_validate_seed_data_no_marker_when_unreachable(tmp_path, monkeypatch):
    _write_seed_copy(tmp_path, monkeypatch, _SAMPLE_SEED)
    from gaming.discovery.asn_bgp import ASNBGPSource

    # Nothing announced -> provider unchecked -> marker must NOT be stamped.
    monkeypatch.setattr(ASNBGPSource, "discover", lambda self: [])
    report = providers.validate_seed_data(timeout=1.0)
    assert report.marker_updated is False
    assert all(not c.checked for c in report.checks)


def test_validate_seed_data_respects_no_marker(tmp_path, monkeypatch):
    _write_seed_copy(tmp_path, monkeypatch, _SAMPLE_SEED)
    from gaming.discovery.asn_bgp import ASNBGPSource
    from gaming.models import IPRecord

    monkeypatch.setattr(
        ASNBGPSource,
        "discover",
        lambda self: [IPRecord(prefix="203.0.113.0/24", source="asn_bgp")],
    )
    report = providers.validate_seed_data(timeout=1.0, update_marker=False)
    assert report.marker_updated is False
    assert providers.seed_last_validated() == ""  # untouched
