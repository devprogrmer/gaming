"""Exhaustive country-wide discovery: enumeration, naming parity, resumability."""

from __future__ import annotations

import json

import pytest

from gaming.discovery import exhaustive as ex
from gaming.discovery.base import DiscoveryContext
from gaming.discovery.resume import ResumeJournal
from gaming.discovery.rir import parse_delegated_networks
from gaming.models import Filters

# A miniature delegated-statistics file. Deliberately mixes:
#  - a famous operator (Cloudflare)
#  - an obscure-but-real one nobody would hand-add to providers.toml
#  - an allocation whose ASN nobody will name
#  - a non-power-of-two host count (3072) that must not lose addresses
#  - an IPv6 allocation
#  - another country's row, which must never leak into an IR sweep
DELEGATED = "\n".join(
    [
        "2|ripencc|20260727|5|19860514|20260727|+0000",
        "ripencc|IR|ipv4|5.22.0.0|3072|20100101|allocated",
        "ripencc|IR|ipv4|185.143.232.0|1024|20150101|allocated",
        "ripencc|IR|ipv4|91.99.0.0|256|20180101|assigned",
        "ripencc|IR|ipv6|2a01:4f8::|32|20120101|allocated",
        "ripencc|DE|ipv4|88.99.0.0|256|20140101|allocated",
        "ripencc|IR|ipv4|10.0.0.0|256|20140101|reserved",
    ]
)

# prefix -> announcing ASN, as RIPEstat network-info would report it.
PREFIX_ASN = {
    "5.22.0.0/21": "44244",
    "5.22.8.0/22": "44244",
    "185.143.232.0/22": "201133",
    "91.99.0.0/24": "999999",
    "2a01:4f8::/32": "24940",
}

# ASN -> RDAP organization. AS999999 is absent on purpose: an allocation whose
# operator no registry will name.
ASN_ORG = {
    "AS44244": ("Fooberg Hosting Ltd", "IR"),
    "AS201133": ("Pars Pardazesh", "IR"),
    "AS24940": ("Hetzner Online GmbH", "DE"),
}


def _context() -> DiscoveryContext:
    return DiscoveryContext(filters=Filters(countries=["IR"]), timeout=1.0)


@pytest.fixture
def sweep_env(tmp_path, monkeypatch):
    """Patch every network egress the sweep uses, and isolate the journal."""
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    calls = {"network_info": [], "rdap": [], "whois": []}

    def fake_get_text(url, **kwargs):
        assert "delegated" in url
        return DELEGATED

    def fake_get_json(url, **kwargs):
        prefix = url.split("resource=")[1]
        calls["network_info"].append(prefix)
        asn = PREFIX_ASN.get(prefix)
        if asn is None:
            raise ex.HTTPError("not found", status=404)
        return {"data": {"asns": [asn]}}

    def fake_lookup_autnum(self, number):
        asn = f"AS{number}"
        calls["rdap"].append(asn)
        return ASN_ORG.get(asn, (None, None))

    monkeypatch.setattr(ex, "get_text", fake_get_text)
    monkeypatch.setattr(ex, "get_json", fake_get_json)
    monkeypatch.setattr(ex.RDAPSource, "_lookup_autnum", fake_lookup_autnum)
    monkeypatch.setattr(
        ex.ExhaustiveSweep, "_whois_org", lambda self, asn: None
    )
    return calls


# ---- enumeration ---------------------------------------------------------
def test_non_power_of_two_range_loses_no_addresses():
    """A 3072-host allocation must summarize to /21 + /22, not a bare /21."""
    nets = [str(n) for n, _cc, _s in parse_delegated_networks(DELEGATED, {"IR"})]
    assert "5.22.0.0/21" in nets
    assert "5.22.8.0/22" in nets
    total = sum(
        n.num_addresses
        for n, _cc, _s in parse_delegated_networks(DELEGATED, {"IR"})
        if n.version == 4 and str(n).startswith("5.22.")
    )
    assert total == 3072


def test_other_countries_and_reserved_rows_are_excluded():
    nets = [str(n) for n, _cc, _s in parse_delegated_networks(DELEGATED, {"IR"})]
    assert "88.99.0.0/24" not in nets  # DE
    assert "10.0.0.0/24" not in nets  # reserved, not allocated/assigned


def test_ipv6_can_be_skipped():
    v6_on = [str(n) for n, _c, _s in parse_delegated_networks(DELEGATED, {"IR"})]
    v6_off = [
        str(n)
        for n, _c, _s in parse_delegated_networks(DELEGATED, {"IR"}, include_ipv6=False)
    ]
    assert "2a01:4f8::/32" in v6_on
    assert "2a01:4f8::/32" not in v6_off


# ---- naming parity: the whole point of the feature -----------------------
def test_obscure_operator_gets_the_same_detail_as_a_famous_one(sweep_env):
    records = ex.ExhaustiveSweep(country="IR", context=_context()).run()
    by_prefix = {r.prefix: r for r in records}

    obscure = by_prefix["5.22.0.0/21"]
    famous = by_prefix["2a01:4f8::/32"]

    # Identical completeness: every field populated for both.
    for rec in (obscure, famous):
        assert rec.prefix
        assert rec.asn
        assert rec.organization
        assert rec.country

    assert obscure.organization == "Fooberg Hosting Ltd"
    assert obscure.asn == "AS44244"
    assert obscure.country == "IR"
    assert famous.organization == "Hetzner Online GmbH"


def test_unnamed_allocation_is_labelled_not_dropped(sweep_env):
    records = ex.ExhaustiveSweep(country="IR", context=_context()).run()
    by_prefix = {r.prefix: r for r in records}
    assert "91.99.0.0/24" in by_prefix
    assert by_prefix["91.99.0.0/24"].organization == ex.UNNAMED_ORG
    assert by_prefix["91.99.0.0/24"].country == "IR"


def test_unrouted_prefix_is_kept_and_is_not_an_error(sweep_env, monkeypatch):
    """A 404 from the ASN lookup means 'nothing announces this', not a failure."""
    monkeypatch.setitem(PREFIX_ASN, "185.143.232.0/22", None)
    PREFIX_ASN.pop("185.143.232.0/22")
    seen = []
    sweep = ex.ExhaustiveSweep(
        country="IR", context=_context(), progress_callback=seen.append
    )
    try:
        records = sweep.run()
    finally:
        PREFIX_ASN["185.143.232.0/22"] = "201133"
    by_prefix = {r.prefix: r for r in records}
    assert by_prefix["185.143.232.0/22"].organization == ex.UNNAMED_ORG
    assert seen[-1].errors == 0


def test_summary_counts_named_and_unnamed(sweep_env):
    records = ex.ExhaustiveSweep(country="IR", context=_context()).run()
    summary = ex.summarize(records)
    assert summary["prefixes"] == 5
    assert summary["unnamed"] == 1
    assert summary["named"] == 4
    assert summary["asns"] == 4  # the unnamed allocation still reports its ASN
    assert summary["organizations"] == 3


def test_asn_organization_is_looked_up_once_per_asn(sweep_env):
    """Two prefixes on AS44244 must cost one RDAP call, not two."""
    ex.ExhaustiveSweep(country="IR", context=_context()).run()
    assert sweep_env["rdap"].count("AS44244") == 1


def test_sweep_never_emits_bundled_sample_data(sweep_env):
    records = ex.ExhaustiveSweep(country="IR", context=_context()).run()
    assert all("(sample)" not in (r.organization or "") for r in records)
    assert all(r.source == "exhaustive" for r in records)


def test_whois_is_used_when_rdap_has_no_name(sweep_env, monkeypatch):
    monkeypatch.setattr(
        ex.ExhaustiveSweep, "_whois_org", lambda self, asn: "Whois-Only Networks"
    )
    records = ex.ExhaustiveSweep(country="IR", context=_context()).run()
    by_prefix = {r.prefix: r for r in records}
    assert by_prefix["91.99.0.0/24"].organization == "Whois-Only Networks"


def test_unreachable_delegated_file_yields_nothing_and_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))

    def boom(url, **kwargs):
        raise ex.HTTPError("503 upstream", status=503)

    monkeypatch.setattr(ex, "get_text", boom)
    assert ex.ExhaustiveSweep(country="IR", context=_context()).run() == []


# ---- resumability --------------------------------------------------------
def test_interrupted_sweep_resumes_without_repeating_lookups(sweep_env):
    """Stop after 3 prefixes; the resumed run must re-query only the rest."""
    sweep = ex.ExhaustiveSweep(country="IR", context=_context())
    partial = []
    for record in sweep.iter_records():
        partial.append(record)
        if len(partial) == 3:
            break  # the generator's finally-block must flush the journal

    first_pass = list(sweep_env["network_info"])
    assert len(first_pass) == 3

    sweep_env["network_info"].clear()
    resumed = []
    sweep2 = ex.ExhaustiveSweep(
        country="IR", context=_context(), progress_callback=resumed.append
    )
    records = sweep2.run()

    assert len(records) == 5
    # Only the two unresolved prefixes hit the network the second time.
    assert len(sweep_env["network_info"]) == 2
    assert not set(sweep_env["network_info"]) & set(first_pass)
    assert resumed[-1].resumed == 3


def test_completed_sweep_clears_its_journal(sweep_env):
    from gaming.interactive import paths

    ex.ExhaustiveSweep(country="IR", context=_context()).run()
    assert not paths.exhaustive_journal_path("IR").exists()


def test_no_resume_ignores_saved_progress(sweep_env):
    sweep = ex.ExhaustiveSweep(country="IR", context=_context())
    for i, _rec in enumerate(sweep.iter_records()):
        if i == 2:
            break
    sweep_env["network_info"].clear()

    ex.ExhaustiveSweep(country="IR", context=_context(), resume=False).run()
    assert len(sweep_env["network_info"]) == 5  # everything re-queried


def test_journal_is_invalidated_when_upstream_dataset_changes(sweep_env, monkeypatch):
    sweep = ex.ExhaustiveSweep(country="IR", context=_context())
    for i, _rec in enumerate(sweep.iter_records()):
        if i == 2:
            break
    sweep_env["network_info"].clear()

    # Same rows, newer publication serial => the saved progress is stale.
    newer = DELEGATED.replace("20260727|5|", "20260728|5|", 1)
    monkeypatch.setattr(ex, "get_text", lambda url, **kw: newer)
    ex.ExhaustiveSweep(country="IR", context=_context()).run()
    assert len(sweep_env["network_info"]) == 5


def test_corrupt_journal_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    from gaming.interactive import paths

    path = paths.exhaustive_journal_path("IR")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all", encoding="utf-8")

    journal = ResumeJournal.load("IR", dataset="ripencc-20260727")
    assert len(journal) == 0


def test_journal_writes_are_atomic(tmp_path, monkeypatch):
    """A flush must never leave a half-written file readers could pick up."""
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    journal = ResumeJournal.load("IR", dataset="d1")
    journal.record("1.2.3.0/24", {"prefix": "1.2.3.0/24", "organization": "X"})
    journal.flush(force=True)

    payload = json.loads(journal.path.read_text(encoding="utf-8"))
    assert payload["country"] == "IR"
    assert "1.2.3.0/24" in payload["entries"]
    # No leftover temp files.
    assert [p.name for p in journal.path.parent.iterdir() if p.suffix == ".tmp"] == []


def test_journal_country_mismatch_starts_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    journal = ResumeJournal.load("IR", dataset="d1")
    journal.record("1.2.3.0/24", {"prefix": "1.2.3.0/24"})
    journal.flush(force=True)
    # Same file, different country claimed => do not trust it.
    raw = json.loads(journal.path.read_text(encoding="utf-8"))
    raw["country"] = "DE"
    journal.path.write_text(json.dumps(raw), encoding="utf-8")
    assert len(ResumeJournal.load("IR", dataset="d1")) == 0
