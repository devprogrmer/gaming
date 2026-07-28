"""CLI and web-dashboard paths for the new discovery/watch/lookup features."""

from __future__ import annotations

import json

import pytest

from gaming.cli import main
from gaming.interactive import ranges as ranges_mod
from gaming.interactive.storage import HistoryStore
from gaming.models import IPRecord
from gaming.web.auth import CredentialStore, RateLimiter
from gaming.web.handlers import Request, WebApp


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


SWEEP_RECORDS = [
    IPRecord(
        prefix="5.22.0.0/21",
        source="exhaustive",
        asn="AS44244",
        organization="Fooberg Hosting Ltd",
        country="IR",
    ),
    IPRecord(
        prefix="91.99.0.0/24",
        source="exhaustive",
        organization="(unnamed / no public org name)",
        country="IR",
    ),
]


# ==========================================================================
# CLI
# ==========================================================================
def test_discover_exhaustive_invokes_the_sweep_and_prints_records(
    monkeypatch, capsys
):
    seen = {}

    class FakeSweep:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            return SWEEP_RECORDS

    monkeypatch.setattr("gaming.discovery.exhaustive.ExhaustiveSweep", FakeSweep)
    assert main(["discover", "--country", "IR", "--exhaustive", "--format", "json"]) == 0

    out = json.loads(capsys.readouterr().out)
    prefixes = {r["prefix"] for r in out}
    assert prefixes == {"5.22.0.0/21", "91.99.0.0/24"}
    assert seen["country"] == "IR"
    assert seen["include_ipv6"] is True
    assert seen["resume"] is True


def test_discover_exhaustive_flags_are_threaded_through(monkeypatch):
    seen = {}

    class FakeSweep:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            return []

    monkeypatch.setattr("gaming.discovery.exhaustive.ExhaustiveSweep", FakeSweep)
    main(["discover", "--country", "IR", "--exhaustive", "--no-ipv6", "--no-resume"])
    assert seen["include_ipv6"] is False
    assert seen["resume"] is False


def test_discover_exhaustive_save_persists_with_the_exhaustive_origin(monkeypatch):
    class FakeSweep:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return SWEEP_RECORDS

    monkeypatch.setattr("gaming.discovery.exhaustive.ExhaustiveSweep", FakeSweep)
    main(["discover", "--country", "IR", "--exhaustive", "--save", "--format", "json"])

    entries = ranges_mod.category_entries("iran_datacenter")
    stored = {e.cidr: e for e in entries}
    assert "5.22.0.0/21" in stored
    # The unnamed allocation must be kept, not dropped.
    assert "91.99.0.0/24" in stored
    assert stored["5.22.0.0/21"].origin == ranges_mod.EXHAUSTIVE_ORIGIN
    assert stored["5.22.0.0/21"].provider == "Fooberg Hosting Ltd"


def test_ip_list_stdout_is_clean_enough_to_redirect(monkeypatch, capsys):
    class FakeSweep:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return [IPRecord(prefix="192.0.2.0/30", source="exhaustive")]

    monkeypatch.setattr("gaming.discovery.exhaustive.ExhaustiveSweep", FakeSweep)
    assert main(
        ["discover", "--country", "IR", "--exhaustive", "--format", "ip-list"]
    ) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["192.0.2.1", "192.0.2.2"]


def test_ip_list_advisory_output_goes_to_stderr(monkeypatch, capsys, tmp_path):
    class FakeSweep:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return [IPRecord(prefix="192.0.2.0/30", source="exhaustive")]

    monkeypatch.setattr("gaming.discovery.exhaustive.ExhaustiveSweep", FakeSweep)
    out_file = tmp_path / "ips.txt"
    main(
        [
            "discover", "--country", "IR", "--exhaustive", "--save",
            "--format", "ip-list", "-o", str(out_file),
        ]
    )
    captured = capsys.readouterr()
    # Every non-IP line must be on stderr so `> ips.txt` stays a pure IP list.
    assert captured.out.splitlines() == ["192.0.2.1", "192.0.2.2"]
    assert "written:" in captured.err
    assert "saved" in captured.err


def test_ip_list_is_offered_by_the_parser():
    from gaming.cli import build_parser

    args = build_parser().parse_args(["discover", "--format", "ip-list"])
    assert args.format == "ip-list"


# ---- check-membership ----------------------------------------------------
def test_check_membership_reports_a_match(capsys):
    ranges_mod.add_custom_range(
        "iran_datacenter", "5.22.0.0/16", country="IR", provider="Fooberg Hosting Ltd"
    )
    assert main(["check-membership", "5.22.7.1"]) == 0
    out = capsys.readouterr().out
    assert "5.22.0.0/16" in out
    assert "Fooberg Hosting Ltd" in out


def test_check_membership_reports_every_overlapping_match(capsys):
    ranges_mod.add_custom_range("iran_datacenter", "5.22.0.0/16", country="IR")
    ranges_mod.add_custom_range("iran_datacenter", "5.22.7.0/24", country="IR")
    main(["check-membership", "5.22.7.1"])
    out = capsys.readouterr().out
    assert "5.22.7.0/24" in out and "5.22.0.0/16" in out
    # Most specific first.
    assert out.index("5.22.7.0/24") < out.index("5.22.0.0/16")


def test_check_membership_exits_nonzero_and_explains_when_not_found(capsys):
    assert main(["check-membership", "203.0.113.7"]) == 1
    out = capsys.readouterr().out
    assert "not inside any stored range" in out
    assert "--live" in out


def test_check_membership_live_flag_queries_the_registry(monkeypatch, capsys):
    monkeypatch.setattr(
        "gaming.interactive.membership.live_lookup",
        lambda ip, timeout=5.0: {
            "ip": ip, "cidr": "203.0.113.0/24",
            "organization": "Obscure BV", "country": "NL", "source": "rdap",
        },
    )
    assert main(["check-membership", "203.0.113.7", "--live"]) == 1
    out = capsys.readouterr().out
    assert "Obscure BV" in out
    assert "203.0.113.0/24" in out


def test_check_membership_json_output(capsys):
    ranges_mod.add_custom_range("iran_datacenter", "5.22.0.0/16", country="IR")
    main(["check-membership", "5.22.7.1", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] is True
    assert payload["matches"][0]["cidr"] == "5.22.0.0/16"


def test_check_membership_rejects_a_bad_address(capsys):
    assert main(["check-membership", "not-an-ip"]) == 2
    assert "invalid IP address" in capsys.readouterr().err


# ---- watch ---------------------------------------------------------------
def test_watch_runs_a_bounded_number_of_iterations(monkeypatch, capsys):
    runs = {"n": 0}

    class FakeLoop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.country = kwargs.get("country", "IR")
            self.interval = kwargs.get("interval_seconds", 0)
            self.scope = kwargs.get("scope", "iran")

        def run_forever(self, *, max_iterations=0):
            runs["n"] = max_iterations

        def stop(self, **kw):
            pass

    monkeypatch.setattr("gaming.interactive.watch.WatchLoop", FakeLoop)
    assert main(["watch", "--country", "IR", "--interval", "30m", "--count", "2"]) == 0
    assert runs["n"] == 2


def test_watch_parses_the_human_interval(monkeypatch):
    seen = {}

    class FakeLoop:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.country = "IR"
            self.interval = kwargs["interval_seconds"]
            self.scope = "iran"

        def run_forever(self, *, max_iterations=0):
            pass

        def stop(self, **kw):
            pass

    monkeypatch.setattr("gaming.interactive.watch.WatchLoop", FakeLoop)
    main(["watch", "--country", "ir", "--interval", "2h", "--count", "1"])
    assert seen["interval_seconds"] == 7200.0
    assert seen["country"] == "ir"


def test_watch_status_reports_when_nothing_is_running(capsys):
    assert main(["watch", "--status"]) == 0
    assert "not running" in capsys.readouterr().out


def test_watch_stop_is_a_no_op_when_nothing_is_running(capsys):
    assert main(["watch", "--stop"]) == 0
    assert "No running background watch" in capsys.readouterr().out


def test_watch_status_reads_the_watch_pid_file_not_the_web_one(monkeypatch, capsys):
    import os

    from gaming.interactive import paths
    from gaming.web import daemon as daemon_mod

    daemon_mod.write_pid(os.getpid(), paths.watch_pid_path())
    main(["watch", "--status"])
    assert "Watch is running" in capsys.readouterr().out
    # The dashboard must still report itself as down.
    main(["web", "--status"])
    assert "not running" in capsys.readouterr().out


def test_watch_refuses_to_start_a_second_daemon(monkeypatch, capsys):
    import os

    from gaming.interactive import paths
    from gaming.web import daemon as daemon_mod

    daemon_mod.write_pid(os.getpid(), paths.watch_pid_path())
    assert main(["watch", "--daemon"]) == 1
    assert "already running" in capsys.readouterr().err


# ==========================================================================
# Web dashboard
# ==========================================================================
@pytest.fixture
def api(tmp_path):
    """An authorized WebApp plus a bearer token, isolated to tmp_path."""
    store = HistoryStore(tmp_path / "h.db")
    creds = CredentialStore(tmp_path / "creds.json")
    creds.ensure_credentials()
    app = WebApp(credentials=creds, store=store, rate_limiter=RateLimiter())
    return app, creds, creds.ensure_credentials()[0].auth_token


def _post(app, creds, path, body):
    creds.ensure_credentials()
    resp = app.handle(
        Request(
            method="POST",
            path=path,
            headers={"authorization": f"Bearer {_bearer(creds)}"},
            body=json.dumps(body).encode(),
        )
    )
    return resp


def _get(app, creds, path, query=None):
    return app.handle(
        Request(
            method="GET",
            path=path,
            query=query or {},
            headers={"authorization": f"Bearer {_bearer(creds)}"},
        )
    )


def _bearer(creds) -> str:
    """The dashboard's automation token."""
    return creds.ensure_credentials()[0].auth_token


def test_web_lookup_ip_finds_a_stored_range(api):
    app, creds, _ = api
    ranges_mod.add_custom_range(
        "iran_datacenter", "5.22.0.0/16", country="IR", provider="Fooberg Hosting Ltd"
    )
    resp = _post(app, creds, "/api/lookup-ip", {"ip": "5.22.7.1"})
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["found"] is True
    assert payload["matches"][0]["cidr"] == "5.22.0.0/16"
    assert payload["matches"][0]["provider"] == "Fooberg Hosting Ltd"


def test_web_lookup_ip_reports_not_found(api):
    app, creds, _ = api
    payload = json.loads(
        _post(app, creds, "/api/lookup-ip", {"ip": "203.0.113.7",
                                             "include_bundled": False}).body
    )
    assert payload["found"] is False
    assert payload["matches"] == []


def test_web_lookup_ip_rejects_garbage(api):
    app, creds, _ = api
    resp = _post(app, creds, "/api/lookup-ip", {"ip": "not-an-ip"})
    assert resp.status == 400


def test_web_lookup_ip_requires_an_ip(api):
    app, creds, _ = api
    assert _post(app, creds, "/api/lookup-ip", {}).status == 400


def test_web_lookup_ip_live_fallback(api, monkeypatch):
    app, creds, _ = api
    monkeypatch.setattr(
        "gaming.interactive.membership.live_lookup",
        lambda ip, **kw: {"ip": ip, "organization": "Obscure BV", "source": "rdap"},
    )
    payload = json.loads(
        _post(
            app, creds, "/api/lookup-ip",
            {"ip": "203.0.113.7", "live": True, "include_bundled": False},
        ).body
    )
    assert payload["found"] is False
    assert payload["live"]["organization"] == "Obscure BV"


def test_web_lookup_ip_requires_auth(api):
    app, _creds, _ = api
    resp = app.handle(
        Request(method="POST", path="/api/lookup-ip", body=b'{"ip": "1.1.1.1"}')
    )
    assert resp.status == 401


def test_web_exhaustive_discovery_runs_as_a_job(api, monkeypatch):
    app, creds, _ = api

    class FakeSweep:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self):
            return SWEEP_RECORDS

    monkeypatch.setattr("gaming.discovery.exhaustive.ExhaustiveSweep", FakeSweep)
    resp = _post(app, creds, "/api/discover-exhaustive", {"country": "IR"})
    job_id = json.loads(resp.body)["job_id"]

    job = app.jobs.get(job_id)
    job.thread.join(timeout=10) if getattr(job, "thread", None) else None
    for _ in range(200):
        job = app.jobs.get(job_id)
        if job.status in ("done", "error"):
            break
        import time

        time.sleep(0.02)

    assert job.status == "done", job.error
    assert job.result["count"] == 2
    assert job.result["summary"]["unnamed"] == 1
    # Persisted by default, including the unnamed allocation.
    stored = {e.cidr for e in ranges_mod.category_entries("iran_datacenter")}
    assert {"5.22.0.0/21", "91.99.0.0/24"} <= stored


def test_web_watch_start_status_stop(api, monkeypatch):
    app, creds, _ = api
    from gaming.web import handlers as handlers_mod

    class FakeLoop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.running = False
            from gaming.interactive.watch import WatchState

            self.state = WatchState(country=kwargs.get("country", "IR"), interval=3600)

        def start(self):
            self.running = True

        def stop(self, **kw):
            self.running = False

    monkeypatch.setattr("gaming.interactive.watch.WatchLoop", FakeLoop)
    monkeypatch.setattr(handlers_mod, "_WATCH_LOOP", None, raising=False)

    started = json.loads(
        _post(app, creds, "/api/watch", {"action": "start", "country": "IR"}).body
    )
    assert started["running"] is True

    status = json.loads(_get(app, creds, "/api/watch").body)
    assert status["running"] is True
    assert status["state"]["country"] == "IR"

    stopped = json.loads(_post(app, creds, "/api/watch", {"action": "stop"}).body)
    assert stopped["stopped"] is True
    assert json.loads(_get(app, creds, "/api/watch").body)["running"] is False


def test_web_watch_refuses_a_second_start(api, monkeypatch):
    app, creds, _ = api
    from gaming.web import handlers as handlers_mod

    class FakeLoop:
        def __init__(self, **kwargs):
            from gaming.interactive.watch import WatchState

            self.running = False
            self.state = WatchState(country="IR", interval=3600)

        def start(self):
            self.running = True

        def stop(self, **kw):
            self.running = False

    monkeypatch.setattr("gaming.interactive.watch.WatchLoop", FakeLoop)
    monkeypatch.setattr(handlers_mod, "_WATCH_LOOP", None, raising=False)
    _post(app, creds, "/api/watch", {"action": "start"})
    assert _post(app, creds, "/api/watch", {"action": "start"}).status == 409
    _post(app, creds, "/api/watch", {"action": "stop"})


def test_web_watch_rejects_an_unknown_action(api):
    app, creds, _ = api
    assert _post(app, creds, "/api/watch", {"action": "explode"}).status == 400


def test_web_watch_status_with_nothing_running(api):
    app, creds, _ = api
    payload = json.loads(_get(app, creds, "/api/watch").body)
    assert payload["running"] is False
    assert payload["daemon"]["running"] is False


def test_web_export_ip_list(api):
    app, creds, _ = api
    from gaming.interactive.classify import GOOD, ProbeResult
    from gaming.interactive.scanner import ScanReport

    report = ScanReport(
        scope="iran",
        results=[
            (ProbeResult(host="5.22.7.1", sent=4, received=4, avg_ms=10.0), GOOD),
            (ProbeResult(host="5.22.7.2", sent=4, received=4, avg_ms=12.0), GOOD),
        ],
    )
    scan_id = app.store.save_scan(report.scope, report.results)

    resp = _get(app, creds, "/api/export", {"kind": ["ip-list"], "scan": [str(scan_id)]})
    assert resp.status == 200
    body = resp.body.decode() if isinstance(resp.body, bytes) else resp.body
    assert body.splitlines() == ["5.22.7.1", "5.22.7.2"]
    assert "text/plain" in resp.headers.get("Content-Type", "")
