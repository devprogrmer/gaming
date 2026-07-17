from __future__ import annotations

import ipaddress

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
