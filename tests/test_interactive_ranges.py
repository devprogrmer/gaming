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
