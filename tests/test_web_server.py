from __future__ import annotations

import json

import pytest

from gaming.interactive.classify import GOOD, CombinedResult, ProbeResult
from gaming.interactive.storage import HistoryStore
from gaming.web.auth import AuthError, CredentialStore, RateLimiter
from gaming.web.handlers import Request, WebApp


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


@pytest.fixture
def env(tmp_path):
    """A WebApp plus its credential store, isolated to tmp_path."""
    store = HistoryStore(tmp_path / "h.db")
    creds = CredentialStore(tmp_path / "creds.json")
    _c, password = creds.ensure_credentials()
    app = WebApp(credentials=creds, store=store, rate_limiter=RateLimiter(max_attempts=3))
    return app, creds, password


def _req(method, path, *, body=None, cookie="", ip="1.2.3.4", bearer=""):
    headers = {}
    if cookie:
        headers["cookie"] = cookie
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
    raw = json.dumps(body).encode() if body is not None else b""
    return Request(method=method, path=path, headers=headers, body=raw, client_ip=ip)


def _login(app, creds, password, ip="1.2.3.4"):
    username = creds.ensure_credentials()[0].username
    resp = app.handle(
        _req("POST", "/api/login", body={"username": username, "password": password}, ip=ip)
    )
    assert resp.status == 200
    cookie = resp.headers["Set-Cookie"].split(";", 1)[0]
    return cookie


# ---- auth ----------------------------------------------------------------
def test_unauthenticated_api_is_401(env):
    app, _creds, _pw = env
    assert app.handle(_req("GET", "/api/history")).status == 401


def test_login_success_sets_cookie_and_authorizes(env):
    app, creds, pw = env
    cookie = _login(app, creds, pw)
    resp = app.handle(_req("GET", "/api/history", cookie=cookie))
    assert resp.status == 200
    assert "scans" in json.loads(resp.body)


def test_login_failure_is_401(env):
    app, creds, _pw = env
    username = creds.ensure_credentials()[0].username
    resp = app.handle(
        _req("POST", "/api/login", body={"username": username, "password": "nope"})
    )
    assert resp.status == 401


def test_login_rate_limited_after_repeated_failures(env):
    app, creds, _pw = env
    username = creds.ensure_credentials()[0].username
    bad = {"username": username, "password": "wrong"}
    # RateLimiter(max_attempts=3): 3 failures then blocked.
    for _ in range(3):
        assert app.handle(_req("POST", "/api/login", body=bad, ip="9.9.9.9")).status == 401
    blocked = app.handle(_req("POST", "/api/login", body=bad, ip="9.9.9.9"))
    assert blocked.status == 429


def test_rate_limit_is_per_ip(env):
    app, creds, pw = env
    username = creds.ensure_credentials()[0].username
    bad = {"username": username, "password": "wrong"}
    for _ in range(3):
        app.handle(_req("POST", "/api/login", body=bad, ip="9.9.9.9"))
    # A different IP is unaffected and can log in.
    ok = app.handle(
        _req("POST", "/api/login", body={"username": username, "password": pw}, ip="8.8.8.8")
    )
    assert ok.status == 200


def test_bearer_token_authorizes_without_session(env):
    app, creds, _pw = env
    token = creds.ensure_credentials()[0].auth_token
    resp = app.handle(_req("GET", "/api/history", bearer=token))
    assert resp.status == 200


def test_bad_bearer_token_is_rejected(env):
    app, _creds, _pw = env
    assert app.handle(_req("GET", "/api/history", bearer="not-the-token")).status == 401


# ---- credential change ---------------------------------------------------
def test_change_credentials_requires_current_password(env):
    app, creds, pw = env
    cookie = _login(app, creds, pw)
    resp = app.handle(
        _req(
            "POST",
            "/api/change-credentials",
            body={
                "current_password": "wrong",
                "new_username": "newadmin",
                "new_password": "Str0ng!Passw0rd",
            },
            cookie=cookie,
        )
    )
    assert resp.status == 400
    assert "incorrect" in json.loads(resp.body)["error"]


def test_change_credentials_applies_and_invalidates_session(env):
    app, creds, pw = env
    cookie = _login(app, creds, pw)
    username = creds.ensure_credentials()[0].username
    resp = app.handle(
        _req(
            "POST",
            "/api/change-credentials",
            body={
                "current_password": pw,
                "new_username": username,
                "new_password": "Str0ng!Passw0rd",
            },
            cookie=cookie,
        )
    )
    assert resp.status == 200
    # Old session cookie is invalid after the secret rotation.
    assert app.handle(_req("GET", "/api/history", cookie=cookie)).status == 401
    # The new password works.
    assert creds.verify_password(username, "Str0ng!Passw0rd")


def test_change_credentials_rejects_weak_password(env):
    app, creds, pw = env
    cookie = _login(app, creds, pw)
    resp = app.handle(
        _req(
            "POST",
            "/api/change-credentials",
            body={"current_password": pw, "new_username": "x", "new_password": "short"},
            cookie=cookie,
        )
    )
    assert resp.status == 400


# ---- session expiry ------------------------------------------------------
def test_session_expires(tmp_path):
    creds = CredentialStore(tmp_path / "creds.json")
    creds.ensure_credentials()
    token = creds.issue_session(now=1000.0)
    assert creds.validate_session(token, now=1000.0) is True
    # 13 hours later (TTL is 12h) the token is invalid.
    assert creds.validate_session(token, now=1000.0 + 13 * 3600) is False


# ---- search --------------------------------------------------------------
def test_search_returns_seed_matches(env, monkeypatch):
    app, creds, pw = env
    cookie = _login(app, creds, pw)

    # Keep discovery offline/deterministic: no live records, seed data only.
    from gaming import pipeline

    monkeypatch.setattr(pipeline, "discover", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "process", lambda recs, *a, **k: list(recs))

    start = app.handle(
        _req("POST", "/api/search", body={"query": "185", "country": "IR"}, cookie=cookie)
    )
    job_id = json.loads(start.body)["job_id"]

    # The job runs on a thread; poll the endpoint until it finishes.
    import time

    result = None
    for _ in range(100):
        resp = app.handle(_req("GET", f"/api/jobs?id={job_id}", cookie=cookie))
        job = json.loads(resp.body)
        if job["status"] in ("done", "error"):
            result = job
            break
        time.sleep(0.02)
    assert result is not None and result["status"] == "done"
    recs = result["result"]["records"]
    assert recs, "expected seed matches for query 185 + IR"
    assert all(r["prefix"].startswith("185") for r in recs)
    assert all(r["country"] == "IR" for r in recs)


# ---- downloads -----------------------------------------------------------
@pytest.fixture
def seeded_scan(env):
    app, creds, pw = env
    p1 = ProbeResult("185.143.232.5", sent=4, received=4, avg_ms=10.0)
    p2 = ProbeResult("5.9.0.5", sent=4, received=4, avg_ms=20.0)
    combined = [
        CombinedResult(p1, abroad_reachable=True, abroad_nodes_ok=4, abroad_nodes_total=4,
                       open_ports=[443]),
        CombinedResult(p2, abroad_reachable=False, abroad_nodes_ok=0, abroad_nodes_total=4),
    ]
    scan_id = app.store.save_scan("iran_cdn", [(p1, GOOD), (p2, GOOD)], combined=combined)
    cookie = _login(app, creds, pw)
    return app, cookie, scan_id


def test_export_csv_headers_and_body(seeded_scan):
    app, cookie, scan_id = seeded_scan
    resp = app.handle(_req("GET", f"/api/export?kind=csv&scan={scan_id}", cookie=cookie))
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in resp.headers["Content-Disposition"]
    assert f"scan-{scan_id}.csv" in resp.headers["Content-Disposition"]
    body = resp.body.decode()
    assert body.splitlines()[0].startswith("source,")
    assert "185.143.232.5" in body


def test_export_json_headers_and_body(seeded_scan):
    app, cookie, scan_id = seeded_scan
    resp = app.handle(_req("GET", f"/api/export?kind=json&scan={scan_id}", cookie=cookie))
    assert resp.headers["Content-Type"].startswith("application/json")
    assert "attachment" in resp.headers["Content-Disposition"]
    data = json.loads(resp.body)
    assert any(r["prefix"].startswith("185.143.232.5") for r in data)


def test_export_whitelist_only_international(seeded_scan):
    app, cookie, scan_id = seeded_scan
    resp = app.handle(
        _req("GET", f"/api/export?kind=whitelist&scan={scan_id}", cookie=cookie)
    )
    assert resp.headers["Content-Type"].startswith("text/plain")
    body = resp.body.decode()
    # Only the INTERNATIONAL host is included; the IRAN_ONLY host is excluded.
    assert body.strip() == "185.143.232.5"


# ---- settings shared with CLI/menu ---------------------------------------
def test_settings_roundtrip_shared_file(seeded_scan):
    app, cookie, _scan_id = seeded_scan
    save = app.handle(
        _req("POST", "/api/settings", body={"max_global_targets": 7}, cookie=cookie)
    )
    assert save.status == 200
    # Reads back through the same settings file the menu uses.
    from gaming.interactive.settings import load_settings

    assert load_settings().max_global_targets == 7


# ---- summary widget ------------------------------------------------------
def test_summary_reports_provider_connectivity(seeded_scan):
    app, cookie, _scan_id = seeded_scan
    resp = app.handle(_req("GET", "/api/summary", cookie=cookie))
    providers = json.loads(resp.body)["providers"]
    # ArvanCloud CDN owns 185.143.232.0/22 -> the INTERNATIONAL host lands there.
    arvan = next((p for p in providers if "ArvanCloud" in p["name"]), None)
    assert arvan is not None
    assert arvan["hosts"] >= 1
    assert arvan["international"] >= 1


# ---- `gaming web` launcher / startup path --------------------------------
def test_serve_prints_credentials_and_url_then_runs(tmp_path, monkeypatch):
    """The `gaming web` entry point must print first-run credentials + the bound
    URL and actually start serving. Regression guard for the broken-launcher
    report: the existing tests only exercised the handler layer, so a failure in
    the serve() startup path (credentials, URL banner, server bind) went
    uncaught. We fake the HTTP server so serve_forever() returns immediately.
    """
    from gaming.web import server

    started = {"served": False, "closed": False}

    class _FakeHTTPD:
        def __init__(self, addr, handler):
            self.socket = object()
            self.address = addr

        def serve_forever(self):
            started["served"] = True

        def server_close(self):
            started["closed"] = True

    monkeypatch.setattr(server, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(server, "_pick_free_port", lambda bind: 31337)
    monkeypatch.setattr(server, "_detect_server_ip", lambda: "203.0.113.7")

    lines: list[str] = []
    rc = server.serve(bind="0.0.0.0", print_fn=lines.append)

    assert rc == 0
    assert started["served"] and started["closed"]
    out = "\n".join(lines)
    # First run: a username and a one-time password are shown.
    assert "Username:" in out
    assert "Password:" in out
    assert "shown ONCE" in out
    # The reachable URL (detected server IP + chosen port) is printed.
    assert "http://203.0.113.7:31337/" in out


# ---- Iran-only location gate (web scan) ----------------------------------
def test_web_scan_iran_category_excludes_non_ir_located(env, monkeypatch):
    """The web scan of an Iranian category must only scan IR-located CIDRs and
    report foreign-located ones separately (Bug 3, web path)."""
    app, creds, pw = env
    cookie = _login(app, creds, pw)

    from gaming.interactive import ranges as ranges_mod

    ranges_mod.save_discovered(
        "iran_datacenter",
        ["185.51.200.0/24", "5.5.5.0/24"],
        metadata={
            "185.51.200.0/24": ("IR", "pars"),   # genuinely Iranian
            "5.5.5.0/24": ("DE", "arvancloud"),   # Iranian org, foreign PoP
        },
    )

    scanned: list[str] = []

    from gaming.interactive import scanner as scanner_mod

    def _fake_scan_hosts(hosts, *, count=4, timeout=2.0, concurrency=32, on_result=None):
        scanned.extend(hosts)
        return []

    monkeypatch.setattr(scanner_mod, "scan_hosts", _fake_scan_hosts)
    # Keep the abroad pass offline.
    monkeypatch.setattr(scanner_mod, "check_abroad", lambda host, **kw: (None, 0, 0))

    start = app.handle(
        _req("POST", "/api/scan", body={"category": "iran_datacenter"}, cookie=cookie)
    )
    job_id = json.loads(start.body)["job_id"]

    import time

    result = None
    for _ in range(100):
        resp = app.handle(_req("GET", f"/api/jobs?id={job_id}", cookie=cookie))
        job = json.loads(resp.body)
        if job["status"] in ("done", "error"):
            result = job
            break
        time.sleep(0.02)
    assert result is not None and result["status"] == "done"
    # The foreign-located CIDR is reported as unverified, not scanned.
    assert "5.5.5.0/24" in result["result"]["location_unverified"]
    assert not any(h.startswith("5.5.5.") for h in scanned)
    assert any(h.startswith("185.51.200.") for h in scanned)


# ---- static + auth-store units -------------------------------------------
def test_password_strength_rules():
    from gaming.web.auth import check_password_strength

    with pytest.raises(AuthError):
        check_password_strength("short")
    with pytest.raises(AuthError):
        check_password_strength("alllowercaseonly")
    # Mixed classes, long enough -> ok.
    check_password_strength("Str0ng!Passw0rd")


def test_asset_loader_blocks_traversal():
    from gaming.web import assets

    assert assets.load_asset("app.css") is not None
    assert assets.load_asset("../secrets") is None
    assert assets.load_asset("a/b.css") is None
