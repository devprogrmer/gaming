from __future__ import annotations

from gaming.models import IPRecord
from gaming.reachability import global_check as gc
from gaming.reachability import local, ports
from gaming.reachability.global_check import (
    ABROAD_NOT_APPLICABLE,
    ABROAD_OK,
    ABROAD_UNAVAILABLE,
    AbroadResult,
    CheckHostProvider,
    RipeAtlasProvider,
    _interpret,
    _is_public,
    build_providers,
    check_abroad,
    combine_results,
    global_reachability,
)


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
    # private host returns (None, 0, 0) without any network call
    assert global_reachability("10.0.0.1") == (None, 0, 0)


def test_interpret_results():
    assert _interpret({"n1": [{"time": 0.1, "address": "1.2.3.4"}]}) == (1, 1)
    assert _interpret({"n1": [{"error": "timeout"}]}) == (0, 1)
    assert _interpret({"n1": None}) == (0, 0)  # all pending
    assert _interpret({"n1": [[["OK", 0.05]]]}) == (1, 1)


def test_interpret_threshold_mix():
    # Two nodes report; one OK, one failed -> 1 of 2.
    ok, total = _interpret(
        {"n1": [{"time": 0.1, "address": "1.2.3.4"}], "n2": [{"error": "timeout"}]}
    )
    assert (ok, total) == (1, 2)


def _stub_global_http(monkeypatch, result_payload):
    """Fake check-host.net: instant start + a single result payload, no sleeps."""
    def _fake_get_json(url, **kw):
        if "/check-tcp" in url or "/check-ping" in url:
            return {"request_id": "req-123"}
        return result_payload

    monkeypatch.setattr(gc, "get_json", _fake_get_json)
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)


def test_global_reachability_meets_threshold(monkeypatch):
    # 1 of 2 nodes OK -> reachable at the default 0.5 fraction.
    _stub_global_http(
        monkeypatch,
        {"n1": [{"time": 0.1, "address": "1.2.3.4"}], "n2": [{"error": "x"}]},
    )
    assert global_reachability("8.8.8.8") == (True, 1, 2)


def test_global_reachability_below_threshold(monkeypatch):
    # 1 of 3 nodes OK -> below 0.5 -> not reachable, counts still returned.
    _stub_global_http(
        monkeypatch,
        {
            "n1": [{"time": 0.1, "address": "1.2.3.4"}],
            "n2": [{"error": "x"}],
            "n3": [{"error": "y"}],
        },
    )
    assert global_reachability("8.8.8.8") == (False, 1, 3)


def test_global_reachability_custom_fraction(monkeypatch):
    _stub_global_http(
        monkeypatch,
        {
            "n1": [{"time": 0.1, "address": "1.2.3.4"}],
            "n2": [{"error": "x"}],
            "n3": [{"error": "y"}],
        },
    )
    # A stricter/looser threshold changes the verdict, not the counts.
    assert global_reachability("8.8.8.8", min_ok_fraction=0.3) == (True, 1, 3)
    assert global_reachability("8.8.8.8", min_ok_fraction=0.9) == (False, 1, 3)


# ---- Part D: provider abstraction + service-unavailable signal ------------
def test_checkhost_provider_success(monkeypatch):
    _stub_global_http(
        monkeypatch,
        {"n1": [{"time": 0.1, "address": "1.2.3.4"}], "n2": [{"error": "x"}]},
    )
    res = CheckHostProvider().check("8.8.8.8")
    assert res.status == ABROAD_OK
    assert res.reachable is True
    assert (res.nodes_ok, res.nodes_total) == (1, 2)


def test_checkhost_provider_non_public_is_not_applicable():
    res = CheckHostProvider().check("10.0.0.1")
    assert res.status == ABROAD_NOT_APPLICABLE
    assert res.reachable is None


def test_checkhost_provider_start_failure_is_unavailable(monkeypatch):
    # The start request raises -> service unavailable, NOT a false "not reachable".
    def _boom(url, **kw):
        raise gc.HTTPError("check-host down")

    monkeypatch.setattr(gc, "get_json", _boom)
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)
    res = CheckHostProvider().check("8.8.8.8")
    assert res.status == ABROAD_UNAVAILABLE
    assert res.reachable is None
    assert (res.nodes_ok, res.nodes_total) == (0, 0)


def test_checkhost_provider_never_answers_is_unavailable(monkeypatch):
    # Start succeeds, but the result endpoint stays fully pending -> unavailable.
    _stub_global_http(monkeypatch, {"n1": None, "n2": None})
    res = CheckHostProvider(poll_attempts=2).check("8.8.8.8")
    assert res.status == ABROAD_UNAVAILABLE


def _stub_atlas_http(monkeypatch, *, create, results):
    def _fake_post_json(url, payload, **kw):
        return create

    def _fake_get_json(url, **kw):
        return results

    monkeypatch.setattr(gc, "post_json", _fake_post_json)
    monkeypatch.setattr(gc, "get_json", _fake_get_json)
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)


def test_ripe_atlas_provider_success(monkeypatch):
    _stub_atlas_http(
        monkeypatch,
        create={"measurements": [12345]},
        results=[{"rcvd": 3, "avg": 25.0}, {"rcvd": 0, "avg": -1}],
    )
    res = RipeAtlasProvider(api_key="k").check("8.8.8.8")
    assert res.status == ABROAD_OK
    assert (res.nodes_ok, res.nodes_total) == (1, 2)
    assert res.reachable is True


def test_ripe_atlas_provider_create_failure_is_unavailable(monkeypatch):
    def _boom(url, payload, **kw):
        raise gc.HTTPError("atlas down")

    monkeypatch.setattr(gc, "post_json", _boom)
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)
    res = RipeAtlasProvider(api_key="k").check("8.8.8.8")
    assert res.status == ABROAD_UNAVAILABLE


def test_ripe_atlas_without_key_is_unavailable_and_skipped(monkeypatch):
    monkeypatch.delenv("GAMING_RIPE_ATLAS_KEY", raising=False)
    # A keyless provider reports unavailable directly...
    assert RipeAtlasProvider(api_key=None).check("8.8.8.8").status == ABROAD_UNAVAILABLE
    # ...and build_providers("both") silently drops it, leaving only check-host.
    provs = build_providers("both")
    assert [p.name for p in provs] == ["check-host"]


def test_build_providers_includes_atlas_with_key():
    provs = build_providers("both", ripe_atlas_key="k")
    assert {p.name for p in provs} == {"check-host", "ripe-atlas"}
    assert [p.name for p in build_providers("ripe-atlas", ripe_atlas_key="k")] == [
        "ripe-atlas"
    ]


# ---- measure_from_near (approximate proximity ping) -----------------------
def _fake_get_json_for_proximity(*, asns, probe_results, poll_results):
    """Dispatch fake GETs by URL shape: RIPEstat network-info, Atlas probe
    search, or Atlas measurement results polling."""

    def _fake(url, **kw):
        if "network-info" in url:
            return {"data": {"asns": asns}} if asns is not None else {"data": {}}
        if "/probes/" in url:
            return {"results": probe_results}
        if "/results/" in url:
            return poll_results
        raise AssertionError(f"unexpected GET {url}")

    return _fake


def test_measure_from_near_nearby_probe_found_and_succeeds(monkeypatch):
    monkeypatch.setattr(
        gc,
        "get_json",
        _fake_get_json_for_proximity(
            asns=["12345"],
            probe_results=[{"id": 999}],
            poll_results=[{"rcvd": 3, "avg": 33.5}],
        ),
    )
    monkeypatch.setattr(gc, "post_json", lambda url, payload, **kw: {"measurements": [55555]})
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)

    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key="k")
    assert res.status == gc.PROXIMITY_OK
    assert res.probe_id == 999
    assert res.probe_asn == "12345"
    assert res.avg_ms == 33.5
    assert res.reachable is True
    # The approximation disclaimer must always be present, never omitted.
    assert "Approximate" in res.note
    assert "not from the IP itself" in res.note


def test_measure_from_near_nearby_probe_found_but_measurement_times_out(monkeypatch):
    monkeypatch.setattr(
        gc,
        "get_json",
        _fake_get_json_for_proximity(
            asns=["12345"], probe_results=[{"id": 999}], poll_results=[]
        ),
    )
    monkeypatch.setattr(gc, "post_json", lambda url, payload, **kw: {"measurements": [55555]})
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)

    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key="k", poll_attempts=2)
    assert res.status == gc.PROXIMITY_UNAVAILABLE
    assert "Approximate" in res.note


def test_measure_from_near_measurement_create_failure_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        gc,
        "get_json",
        _fake_get_json_for_proximity(
            asns=["12345"], probe_results=[{"id": 999}], poll_results=[]
        ),
    )

    def _boom(url, payload, **kw):
        raise gc.HTTPError("atlas down")

    monkeypatch.setattr(gc, "post_json", _boom)
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)

    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key="k")
    assert res.status == gc.PROXIMITY_UNAVAILABLE


def test_measure_from_near_no_nearby_probe_found(monkeypatch):
    monkeypatch.setattr(
        gc,
        "get_json",
        _fake_get_json_for_proximity(asns=["12345"], probe_results=[], poll_results=[]),
    )
    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key="k")
    assert res.status == gc.PROXIMITY_NO_PROBE
    assert "No RIPE Atlas probe was found" in res.note
    assert "Approximate" in res.note


def test_measure_from_near_unresolvable_source_asn_is_no_nearby_probe(monkeypatch):
    monkeypatch.setattr(
        gc,
        "get_json",
        _fake_get_json_for_proximity(asns=None, probe_results=[], poll_results=[]),
    )
    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key="k")
    assert res.status == gc.PROXIMITY_NO_PROBE


def test_measure_from_near_network_info_lookup_failure_is_unavailable(monkeypatch):
    def _boom(url, **kw):
        raise gc.HTTPError("ripestat down")

    monkeypatch.setattr(gc, "get_json", _boom)
    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key="k")
    assert res.status == gc.PROXIMITY_UNAVAILABLE


def test_measure_from_near_without_key_is_unavailable_and_makes_no_http_calls(monkeypatch):
    monkeypatch.delenv("GAMING_RIPE_ATLAS_KEY", raising=False)

    def _boom(*a, **k):
        raise AssertionError("must not make an HTTP call without an API key")

    monkeypatch.setattr(gc, "get_json", _boom)
    monkeypatch.setattr(gc, "post_json", _boom)

    res = gc.measure_from_near("185.1.1.1", "8.8.8.8", api_key=None)
    assert res.status == gc.PROXIMITY_UNAVAILABLE
    assert "not configured" in res.note.lower()


def test_measure_from_near_invalid_ip_is_unavailable():
    res = gc.measure_from_near("not-an-ip", "8.8.8.8", api_key="k")
    assert res.status == gc.PROXIMITY_UNAVAILABLE


def test_combine_results_sums_node_counts():
    # Two providers: 1/2 and 2/2 -> combined 3/4 -> >=0.5 reachable.
    combined = combine_results(
        [AbroadResult.ok(False, 1, 2), AbroadResult.ok(True, 2, 2)],
        min_ok_fraction=0.5,
    )
    assert combined.status == ABROAD_OK
    assert (combined.nodes_ok, combined.nodes_total) == (3, 4)
    assert combined.reachable is True


def test_combine_results_one_ok_one_unavailable():
    # A provider outage doesn't erase the other's real answer.
    combined = combine_results(
        [AbroadResult.ok(True, 2, 2), AbroadResult.unavailable()],
        min_ok_fraction=0.5,
    )
    assert combined.status == ABROAD_OK
    assert (combined.nodes_ok, combined.nodes_total) == (2, 2)


def test_combine_results_all_unavailable():
    combined = combine_results(
        [AbroadResult.unavailable(), AbroadResult.unavailable()]
    )
    assert combined.status == ABROAD_UNAVAILABLE
    assert combined.reachable is None


def test_combine_results_all_not_applicable():
    combined = combine_results([AbroadResult.not_applicable()])
    assert combined.status == ABROAD_NOT_APPLICABLE


def test_check_abroad_provider_exception_becomes_unavailable(monkeypatch):
    class _Boom:
        name = "boom"

        def check(self, host, **kw):
            raise RuntimeError("kaboom")

    res = check_abroad("8.8.8.8", providers=[_Boom()])
    assert res.status == ABROAD_UNAVAILABLE
