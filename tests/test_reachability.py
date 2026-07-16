from __future__ import annotations

from gaming.models import IPRecord
from gaming.reachability import local, ports
from gaming.reachability.global_check import _interpret, _is_public, global_reachability


def test_check_alive_tcp_success(monkeypatch):
    monkeypatch.setattr(local, "_tcp_connect", lambda h, p, t: True)
    assert local.check_alive("1.2.3.4", method="tcp") is True


def test_check_alive_auto_falls_back_to_tcp(monkeypatch):
    monkeypatch.setattr(local, "_ping", lambda h, t: False)
    monkeypatch.setattr(local, "_tcp_connect", lambda h, p, t: True)
    assert local.check_alive("1.2.3.4", method="auto") is True


def test_check_alive_bulk_sets_status(monkeypatch, sample_records):
    monkeypatch.setattr(local, "check_alive", lambda host, **kw: True)
    out = local.check_alive_bulk(sample_records, concurrency=4)
    assert all(r.alive is True for r in out)


def test_check_alive_bulk_handles_errors(monkeypatch):
    def boom(host, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(local, "check_alive", boom)
    recs = [IPRecord(prefix="1.2.3.0/24")]
    out = local.check_alive_bulk(recs)
    assert out[0].alive is False
    assert "error" in out[0].notes


def test_probe_ports(monkeypatch):
    open_set = {80}
    monkeypatch.setattr(
        ports, "probe_port", lambda host, port, timeout=3.0: port in open_set
    )
    result = ports.probe_ports("1.2.3.4", [22, 80, 443])
    assert result == [80]


def test_probe_ports_empty():
    assert ports.probe_ports("1.2.3.4", []) == []


def test_global_is_public():
    assert _is_public("8.8.8.8") is True
    assert _is_public("192.168.1.1") is False
    assert _is_public("127.0.0.1") is False
    assert _is_public("not-an-ip") is False


def test_global_skips_private():
    # private host returns None without any network call
    assert global_reachability("10.0.0.1") is None


def test_interpret_results():
    assert _interpret({"n1": [{"time": 0.1, "address": "1.2.3.4"}]}) is True
    assert _interpret({"n1": [{"error": "timeout"}]}) is False
    assert _interpret({"n1": None}) is None  # all pending
    assert _interpret({"n1": [[["OK", 0.05]]]}) is True
