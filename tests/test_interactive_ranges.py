from __future__ import annotations

import ipaddress

import pytest

from gaming.interactive import ranges


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Point the app home at a temp dir so custom ranges never touch real state."""
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


def test_bundled_ranges_load():
    iran = ranges.load_ranges("iran")
    foreign = ranges.load_ranges("foreign")
    assert iran, "expected bundled Iranian ranges"
    assert foreign, "expected bundled foreign ranges"
    # Every entry must be a valid CIDR.
    for cidr in iran + foreign:
        ipaddress.ip_network(cidr, strict=False)


def test_unknown_scope_raises():
    with pytest.raises(ValueError):
        ranges.load_ranges("mars")


def test_add_and_remove_custom_range():
    added = ranges.add_custom_range("iran", "203.0.113.0/24")
    assert added == "203.0.113.0/24"
    assert "203.0.113.0/24" in ranges.custom_ranges("iran")
    # It also appears in the merged list.
    assert "203.0.113.0/24" in ranges.load_ranges("iran")

    assert ranges.remove_custom_range("iran", "203.0.113.0/24") is True
    assert "203.0.113.0/24" not in ranges.custom_ranges("iran")
    # Removing again is a no-op.
    assert ranges.remove_custom_range("iran", "203.0.113.0/24") is False


def test_add_invalid_range_raises():
    with pytest.raises(ValueError):
        ranges.add_custom_range("iran", "not-a-cidr")


def test_add_rejects_unknown_scope():
    with pytest.raises(ValueError):
        ranges.add_custom_range("mars", "203.0.113.0/24")


def test_custom_range_dedup():
    ranges.add_custom_range("foreign", "198.51.100.0/24")
    ranges.add_custom_range("foreign", "198.51.100.0/24")
    assert ranges.custom_ranges("foreign").count("198.51.100.0/24") == 1


# ---- category storage ----------------------------------------------------
def test_save_discovered_by_category_and_dedup():
    added = ranges.save_discovered("foreign_cdn", ["1.1.1.0/24", "1.1.1.0/24"])
    assert added == 1  # deduped within the call
    assert ranges.load_category("foreign_cdn") == ["1.1.1.0/24"]
    # Saving the same CIDR again adds nothing.
    assert ranges.save_discovered("foreign_cdn", ["1.1.1.0/24"]) == 0


def test_save_discovered_rejects_scope():
    with pytest.raises(ValueError):
        ranges.save_discovered("iran", ["1.1.1.0/24"])  # not a category


def test_categories_are_separate():
    ranges.save_discovered("iran_datacenter", ["185.51.200.0/22"])
    ranges.save_discovered("iran_cdn", ["185.143.232.0/22"])
    ranges.save_discovered("foreign_datacenter", ["5.9.0.0/16"])
    ranges.save_discovered("foreign_cdn", ["104.16.0.0/13"])
    assert ranges.load_category("iran_datacenter") == ["185.51.200.0/22"]
    assert ranges.load_category("iran_cdn") == ["185.143.232.0/22"]
    assert ranges.load_category("foreign_datacenter") == ["5.9.0.0/16"]
    assert ranges.load_category("foreign_cdn") == ["104.16.0.0/13"]
    # No cross-contamination.
    assert "104.16.0.0/13" not in ranges.load_category("iran_cdn")


def test_load_scope_group_unions_categories():
    ranges.save_discovered("iran_datacenter", ["185.51.200.0/22"])
    ranges.save_discovered("iran_cdn", ["185.143.232.0/22"])
    merged = ranges.load_scope_group("iran")
    assert "185.51.200.0/22" in merged
    assert "185.143.232.0/22" in merged


def test_category_entries_carry_metadata():
    ranges.save_discovered(
        "foreign_cdn",
        ["1.1.1.0/24"],
        metadata={"1.1.1.0/24": ("US", "cloudflare")},
    )
    entries = ranges.category_entries("foreign_cdn")
    assert entries[0].country == "US"
    assert entries[0].provider == "cloudflare"
    assert entries[0].origin == "discovered"


def test_persist_records_classifies_and_saves():
    from gaming.models import IPRecord

    recs = [
        IPRecord(prefix="104.16.0.0/13", country="US", organization="Cloudflare"),
        IPRecord(prefix="5.9.0.0/16", country="DE", organization="Hetzner Hosting"),
        IPRecord(prefix="185.143.232.0/22", country="IR", organization="ArvanCloud CDN"),
    ]
    added = ranges.persist_records(recs)
    assert added.get("foreign_cdn") == 1
    assert added.get("foreign_datacenter") == 1
    assert added.get("iran_cdn") == 1


def test_legacy_two_field_file_still_parses(tmp_path, monkeypatch):
    # Simulate an old custom_ranges.txt with the legacy 'scope,cidr' format.
    home = tmp_path / "legacy"
    home.mkdir()
    monkeypatch.setenv("GAMING_HOME", str(home))
    (home / "custom_ranges.txt").write_text(
        "iran,203.0.113.0/24\nforeign,198.51.100.0/24\n", encoding="utf-8"
    )
    # Legacy scopes still load, defaulting origin to custom.
    assert "203.0.113.0/24" in ranges.custom_ranges("iran")
    assert "198.51.100.0/24" in ranges.custom_ranges("foreign")


def test_remove_custom_range_by_category():
    ranges.save_discovered("foreign_cdn", ["1.1.1.0/24"])
    assert ranges.remove_custom_range("foreign_cdn", "1.1.1.0/24") is True
    assert ranges.load_category("foreign_cdn") == []
    assert ranges.remove_custom_range("foreign_cdn", "1.1.1.0/24") is False


def test_expand_hosts_respects_sample_and_cap():
    hosts = ranges.expand_hosts(
        ["10.0.0.0/24", "10.0.1.0/24"], sample_per_range=4, max_hosts=6
    )
    # 4 + 4 = 8 candidates, capped at 6.
    assert len(hosts) == 6
    for h in hosts:
        ipaddress.ip_address(h)


def test_expand_hosts_single_ip():
    hosts = ranges.expand_hosts(["192.0.2.5/32"], sample_per_range=8, max_hosts=100)
    assert hosts == ["192.0.2.5"]


def test_expand_hosts_stride_covers_range():
    # sample_per_range far smaller than range -> evenly spaced, spans the block.
    hosts = ranges.expand_hosts(["10.0.0.0/24"], sample_per_range=2, max_hosts=100)
    assert len(hosts) == 2
    assert hosts[0] == "10.0.0.1"  # first usable host


def test_expand_hosts_skips_invalid_cidr():
    hosts = ranges.expand_hosts(["garbage", "192.0.2.0/30"], sample_per_range=0, max_hosts=100)
    assert all(h.startswith("192.0.2.") for h in hosts)


def test_expand_hosts_large_ipv4_is_cheap():
    # A /13 has ~500k hosts — must be sampled by arithmetic, not materialized.
    hosts = ranges.expand_hosts(["2.144.0.0/13"], sample_per_range=8, max_hosts=100)
    assert len(hosts) == 8
    for h in hosts:
        addr = ipaddress.ip_address(h)
        assert addr in ipaddress.ip_network("2.144.0.0/13")


def test_expand_hosts_large_ipv6_does_not_hang():
    # A /29 IPv6 range is astronomically large; sampling must stay bounded.
    hosts = ranges.expand_hosts(["2a01:4f8::/29"], sample_per_range=4, max_hosts=100)
    assert len(hosts) == 4
    net = ipaddress.ip_network("2a01:4f8::/29")
    for h in hosts:
        assert ipaddress.ip_address(h) in net
