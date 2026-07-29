"""Tests for on-demand provider lookup by organization name.

The JSON fixtures mirror the real shapes captured from the live registries:
ARIN returns ``ipSearchResults`` with ``cidr0_cidrs``; RIPE returns
``entitySearchResults`` of bare handles that must each be followed to an entity
object carrying ``networks``.
"""

from __future__ import annotations

import pytest

from gaming.discovery import provider_lookup
from gaming.utils.http import HTTPError

ARIN_URL = "https://rdap.arin.net/registry/ips?name="
RIPE_ENTITIES_URL = "https://rdap.db.ripe.net/entities?fn="
RIPE_ENTITY_URL = "https://rdap.db.ripe.net/entity/"


def _arin_net(name, v4prefix, length, org=None, country_label=None):
    """One ARIN network object.

    Mirrors the real payload: the organization is a nested entity carrying the
    registrant role and ``kind: org``, alongside individual contacts whose
    names must NOT be mistaken for the company.
    """
    net = {
        "handle": f"NET-{v4prefix.replace('.', '-')}",
        "name": name,
        "cidr0_cidrs": [{"v4prefix": v4prefix, "length": length}],
        "country": "US",
    }
    if org:
        adr_params = {"label": f"1 Example St\n{country_label}"} if country_label else {}
        net["entities"] = [
            {
                "handle": "ADMIN-1",
                "roles": ["administrative"],
                "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                         ["kind", {}, "text", "individual"],
                                         ["fn", {}, "text", "Jane Contact"]]],
            },
            {
                "handle": "ORG-1",
                "roles": ["registrant"],
                "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                         ["adr", adr_params, "text", ["", ""]],
                                         ["kind", {}, "text", "org"],
                                         ["fn", {}, "text", org]]],
            },
        ]
    return net


def _ripe_entity(handle, org, nets):
    return {
        "handle": handle,
        "objectClassName": "entity",
        "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                 ["fn", {}, "text", org]]],
        "networks": [
            {
                "handle": f"{v4}-net",
                "name": org.upper().replace(" ", ""),
                "country": "DE",
                "cidr0_cidrs": [{"v4prefix": v4, "length": ln}],
            }
            for v4, ln in nets
        ],
    }


@pytest.fixture
def fake_rdap(monkeypatch):
    """Route get_json to a routing table keyed by URL prefix."""
    routes: dict[str, object] = {}

    def _get_json(url, *, timeout=5.0, retries=2, headers=None):
        for prefix, value in routes.items():
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise HTTPError(f"GET {url} failed: no route", status=404)

    monkeypatch.setattr(provider_lookup, "get_json", _get_json)
    return routes


def test_unseeded_provider_is_found_via_arin(fake_rdap):
    """A real company absent from providers.toml must still resolve.

    This is the exact failure being fixed: 'Zenlayer' is a real registered
    organization that the bundled seed file has never heard of, and every
    previous code path returned 'No records.' for it.
    """
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [
            _arin_net("ZENLAYER", "107.151.192.0", 24, org="Zenlayer Inc."),
            _arin_net("ZENLAYER-104", "107.151.196.0", 22),
        ]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Zenlayer", timeout=1)

    assert result.found
    prefixes = {r.prefix for r in result.records}
    assert prefixes == {"107.151.192.0/24", "107.151.196.0/22"}
    assert all(r.source == "rdap-name" for r in result.records)
    assert result.records[0].organization == "Zenlayer Inc."
    assert result.records[0].country == "US"
    assert "2 range(s)" in result.summary()


def test_ripe_entities_are_followed_to_their_networks(fake_rdap):
    """RIPE only answers entity search, so each handle must be followed."""
    fake_rdap[ARIN_URL] = HTTPError("GET failed: HTTP Error 404: ", status=404)
    fake_rdap[RIPE_ENTITIES_URL] = {
        "entitySearchResults": [{"handle": "ORG-ZI112-RIPE"}]
    }
    fake_rdap[RIPE_ENTITY_URL] = _ripe_entity(
        "ORG-ZI112-RIPE", "Zenlayer Inc.",
        [("62.115.250.0", 24), ("80.239.191.0", 24)],
    )

    result = provider_lookup.lookup_provider_by_name("Zenlayer", timeout=1)

    assert result.found
    assert {r.prefix for r in result.records} == {
        "62.115.250.0/24", "80.239.191.0/24"
    }
    assert result.records[0].organization == "Zenlayer Inc."
    assert result.records[0].country == "DE"


def test_results_from_both_registries_merge_and_dedupe(fake_rdap):
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [_arin_net("ACME", "203.0.113.0", 24, org="Acme Corp")]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": [{"handle": "ORG-A1"}]}
    fake_rdap[RIPE_ENTITY_URL] = _ripe_entity(
        "ORG-A1", "Acme Corp", [("203.0.113.0", 24), ("198.51.100.0", 24)]
    )

    result = provider_lookup.lookup_provider_by_name("Acme", timeout=1)

    # The overlapping prefix appears once, not twice.
    assert [r.prefix for r in result.records] == [
        "203.0.113.0/24", "198.51.100.0/24"
    ]
    assert result.sources_queried == ["arin", "ripe"]


def test_nonsense_name_reports_not_found_clearly(fake_rdap):
    """A name with no matches must say so, not fail silently."""
    fake_rdap[ARIN_URL] = HTTPError("GET failed: HTTP Error 404: ", status=404)
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Zzqxnope", timeout=1)

    assert not result.found
    assert not result.all_sources_failed
    assert result.errors == []
    summary = result.summary()
    assert "No organization matching 'Zzqxnope'" in summary
    assert "ARIN and RIPE" in summary


def test_registry_outage_is_not_reported_as_not_found(fake_rdap):
    """A total outage must be distinguishable from 'this provider is unknown'.

    Reporting an unreachable registry as an empty result is precisely how the
    old behaviour misled the user.
    """
    fake_rdap[ARIN_URL] = HTTPError("GET failed: timed out", status=None)
    fake_rdap[RIPE_ENTITIES_URL] = HTTPError("GET failed: HTTP Error 500: ", status=500)

    result = provider_lookup.lookup_provider_by_name("Zenlayer", timeout=1)

    assert not result.found
    assert result.all_sources_failed
    assert len(result.errors) == 2
    assert "Could not reach any registry" in result.summary()


def test_one_registry_down_still_returns_the_other(fake_rdap):
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [_arin_net("ACME", "203.0.113.0", 24, org="Acme Corp")]
    }
    fake_rdap[RIPE_ENTITIES_URL] = HTTPError("GET failed: HTTP Error 500: ", status=500)

    result = provider_lookup.lookup_provider_by_name("Acme", timeout=1)

    assert result.found
    assert result.sources_queried == ["arin"]
    assert len(result.errors) == 1
    assert not result.all_sources_failed


def test_name_matching_several_organizations_returns_all(fake_rdap):
    """A shared name must not collapse to just the first organization."""
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [
            _arin_net("APEX-US", "203.0.113.0", 24, org="Apex Networks LLC"),
            _arin_net("APEX-CA", "198.51.100.0", 24, org="Apex Hosting Canada"),
        ]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": [{"handle": "ORG-AX"}]}
    fake_rdap[RIPE_ENTITY_URL] = _ripe_entity(
        "ORG-AX", "Apex Systems GmbH", [("192.0.2.0", 24)]
    )

    result = provider_lookup.lookup_provider_by_name("Apex", timeout=1)

    assert set(result.organizations) == {
        "Apex Networks LLC", "Apex Hosting Canada", "Apex Systems GmbH",
    }
    assert len(result.records) == 3
    assert "3 organization(s)" in result.summary()


def test_results_bypass_the_datacenter_keyword_classifier(fake_rdap):
    """An explicitly-named org must survive even without a hosting keyword.

    _DATACENTER_KEYWORDS would drop a name like 'Zenlayer Inc.' from any
    category-scoped path; a direct lookup by name must not be subject to it.
    """
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [
            _arin_net("QUIET", "203.0.113.0", 24, org="Quiet Meadow Industries")
        ]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name(
        "Quiet Meadow Industries", timeout=1
    )

    assert result.found
    assert result.records[0].organization == "Quiet Meadow Industries"


def test_individual_contact_is_not_mistaken_for_the_organization(fake_rdap):
    """A personal admin contact must never be reported as the company.

    Real ARIN payloads nest an individual abuse/admin contact ahead of the
    organization; naively taking the first `fn` reports a stranger's name (e.g.
    "qu ming") as the provider. The registrant org must win, and when no org
    entity exists the network's own name is used instead.
    """
    net = {
        "handle": "NET-1",
        "name": "ZENLAYER",
        "cidr0_cidrs": [{"v4prefix": "107.151.192.0", "length": 24}],
        "entities": [
            {
                "handle": "PERSON-1",
                "roles": ["abuse", "administrative", "technical"],
                "vcardArray": ["vcard", [["kind", {}, "text", "individual"],
                                         ["fn", {}, "text", "qu ming"]]],
                # ARIN nests the real org one level deeper.
                "entities": [
                    {
                        "handle": "ORG-Z",
                        "roles": ["registrant"],
                        "vcardArray": ["vcard", [["kind", {}, "text", "org"],
                                                 ["fn", {}, "text", "Zenlayer Inc."]]],
                    }
                ],
            },
        ],
    }
    fake_rdap[ARIN_URL] = {"ipSearchResults": [net]}
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Zenlayer", timeout=1)
    assert result.records[0].organization == "Zenlayer Inc."


def test_network_name_is_used_when_no_org_entity_exists(fake_rdap):
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [{
            "name": "VULTR",
            "cidr0_cidrs": [{"v4prefix": "216.238.64.0", "length": 19}],
            "entities": [{
                "roles": ["technical"],
                "vcardArray": ["vcard", [["kind", {}, "text", "individual"],
                                         ["fn", {}, "text", "Dave Aninowsky"]]],
            }],
        }]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Vultr", timeout=1)
    assert result.records[0].organization == "VULTR"


def test_registrant_address_is_noted_when_no_country_field(fake_rdap):
    """ARIN gives no country; record the registrant's location as text.

    Inventing an ISO code from a postal address would be a guess, so the
    address line is kept in notes and `country` stays honestly unset.
    """
    net = _arin_net("ACME", "203.0.113.0", 24, org="Acme Corp",
                    country_label="United States")
    del net["country"]
    fake_rdap[ARIN_URL] = {"ipSearchResults": [net]}
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Acme", timeout=1)
    rec = result.records[0]
    assert rec.country is None
    assert "United States" in rec.notes


def test_empty_name_is_rejected_without_a_network_call(fake_rdap):
    result = provider_lookup.lookup_provider_by_name("   ", timeout=1)
    assert not result.found
    assert result.errors == ["no provider name given"]
    assert result.sources_queried == []


def test_limit_caps_the_number_of_records(fake_rdap):
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [
            _arin_net(f"N{i}", f"203.0.{i}.0", 24, org="Big Corp") for i in range(50)
        ]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Big", timeout=1, limit=5)
    assert len(result.records) == 5


def test_malformed_registry_payloads_are_skipped_not_fatal(fake_rdap):
    """Fail-soft: garbage in one entry must not lose the valid ones."""
    fake_rdap[ARIN_URL] = {
        "ipSearchResults": [
            "not-a-dict",
            {"name": "NO-CIDR"},
            {"cidr0_cidrs": [{"v4prefix": "not-an-ip", "length": 24}]},
            _arin_net("GOOD", "203.0.113.0", 24, org="Good Corp"),
        ]
    }
    fake_rdap[RIPE_ENTITIES_URL] = {"entitySearchResults": []}

    result = provider_lookup.lookup_provider_by_name("Mixed", timeout=1)
    assert [r.prefix for r in result.records] == ["203.0.113.0/24"]
