"""Request routing, session middleware, and JSON/handler logic for the dashboard.

This module is transport-agnostic: it works on plain :class:`Request` /
:class:`Response` dataclasses, so the whole dashboard can be driven in tests
without opening a socket. :mod:`gaming.web.server` is the thin adapter that maps
``http.server`` requests onto :class:`WebApp.handle`.

Every route delegates to existing modules — ``pipeline``/``providers``/
``ranges``/``scanner``/``storage``/``reporting`` — so there is no business logic
duplicated here, only HTTP glue, auth enforcement, and JSON shaping.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..logging_setup import get_logger
from .auth import AuthError, CredentialStore, RateLimiter
from .jobs import JobManager

log = get_logger("gaming.web.handlers")

_SESSION_COOKIE = "gaming_session"


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    client_ip: str = "0.0.0.0"

    def query_one(self, key: str, default: str = "") -> str:
        vals = self.query.get(key)
        return vals[0] if vals else default

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def cookie(self, name: str) -> str:
        raw = self.headers.get("cookie") or self.headers.get("Cookie")
        if not raw:
            return ""
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - malformed cookies are just "absent"
            return ""
        morsel = jar.get(name)
        return morsel.value if morsel else ""

    def bearer(self) -> str:
        auth = self.headers.get("authorization") or self.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""


@dataclass(slots=True)
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, *, status: int = 200) -> Response:
        body = json.dumps(data).encode("utf-8")
        return cls(status=status, body=body, headers={"Content-Type": "application/json"})

    @classmethod
    def text(
        cls, text: str, *, status: int = 200, content_type: str = "text/plain"
    ) -> Response:
        return cls(
            status=status,
            body=text.encode("utf-8"),
            headers={"Content-Type": f"{content_type}; charset=utf-8"},
        )

    @classmethod
    def download(cls, text: str, *, filename: str, content_type: str) -> Response:
        return cls(
            status=200,
            body=text.encode("utf-8"),
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


Handler = Callable[["WebApp", Request], Response]

# Routes that do not require an authenticated session.
_PUBLIC_PATHS = {"/login", "/api/login", "/health"}
_STATIC_PREFIX = "/static/"


class WebApp:
    """The dashboard application: routing + auth + delegation to core modules."""

    def __init__(
        self,
        *,
        credentials: CredentialStore | None = None,
        jobs: JobManager | None = None,
        rate_limiter: RateLimiter | None = None,
        store: Any = None,
    ) -> None:
        self.credentials = credentials or CredentialStore()
        self.jobs = jobs or JobManager()
        self.rate_limiter = rate_limiter or RateLimiter()
        # Lazily import to avoid pulling sqlite/interactive at module import time.
        if store is None:
            from ..interactive.storage import HistoryStore

            store = HistoryStore()
        self.store = store
        self._routes: dict[tuple[str, str], Handler] = {}
        self._register_routes()

    # ---- routing ---------------------------------------------------------
    def route(self, method: str, path: str, handler: Handler) -> None:
        self._routes[(method.upper(), path)] = handler

    def handle(self, request: Request) -> Response:
        """Top-level entry: enforce auth, dispatch, and never leak a traceback."""
        parsed = urlparse(request.path)
        clean_path = parsed.path
        if not request.query:
            request.query = parse_qs(parsed.query)

        try:
            if not self._is_authorized(request, clean_path):
                if clean_path.startswith("/api/"):
                    return Response.json({"error": "unauthorized"}, status=401)
                return _redirect("/login")

            handler = self._routes.get((request.method.upper(), clean_path))
            if handler is None:
                # Static assets and the SPA shell are resolved by the server
                # adapter; here, an unknown API path is a 404.
                return Response.json({"error": "not found"}, status=404)
            return handler(self, request)
        except Exception as exc:  # noqa: BLE001 - a handler bug must not crash server
            log.warning("handler error for %s %s: %s", request.method, clean_path, exc)
            return Response.json({"error": "internal error"}, status=500)

    def _is_authorized(self, request: Request, path: str) -> bool:
        if path in _PUBLIC_PATHS or path == "/" or path.startswith(_STATIC_PREFIX):
            return True
        # Bearer token (automation) OR a valid session cookie.
        if self.credentials.verify_bearer(request.bearer()):
            return True
        return self.credentials.validate_session(request.cookie(_SESSION_COOKIE))

    def _register_routes(self) -> None:
        self.route("POST", "/api/login", _login)
        self.route("POST", "/api/logout", _logout)
        self.route("GET", "/api/me", _me)
        self.route("POST", "/api/change-credentials", _change_credentials)
        self.route("POST", "/api/search", _start_search)
        self.route("GET", "/api/jobs", _get_job)  # ?id=...
        self.route("POST", "/api/scan", _run_scan)
        self.route("GET", "/api/history", _history)
        self.route("GET", "/api/scan-results", _scan_results)  # ?id=...
        self.route("GET", "/api/settings", _get_settings)
        self.route("POST", "/api/settings", _save_settings)
        self.route("GET", "/api/summary", _summary)
        self.route("GET", "/api/export", _export)  # ?kind=csv|json|whitelist&scan=...


def _redirect(location: str) -> Response:
    return Response(status=302, headers={"Location": location})


# --------------------------------------------------------------------------
# Auth handlers
# --------------------------------------------------------------------------
def _login(app: WebApp, req: Request) -> Response:
    if app.rate_limiter.is_blocked(req.client_ip):
        return Response.json(
            {"error": "too many attempts; try again later"}, status=429
        )
    data = req.json()
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    if app.credentials.verify_password(username, password):
        app.rate_limiter.reset(req.client_ip)
        token = app.credentials.issue_session()
        resp = Response.json({"ok": True})
        cookie = (
            f"{_SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"
        )
        resp.headers["Set-Cookie"] = cookie
        return resp
    app.rate_limiter.record_failure(req.client_ip)
    return Response.json({"error": "invalid credentials"}, status=401)


def _logout(app: WebApp, req: Request) -> Response:
    resp = Response.json({"ok": True})
    resp.headers["Set-Cookie"] = (
        f"{_SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    )
    return resp


def _me(app: WebApp, req: Request) -> Response:
    creds, _ = app.credentials.ensure_credentials()
    return Response.json({"username": creds.username})


def _change_credentials(app: WebApp, req: Request) -> Response:
    data = req.json()
    try:
        app.credentials.change_credentials(
            str(data.get("current_password", "")),
            str(data.get("new_username", "")),
            str(data.get("new_password", "")),
        )
    except AuthError as exc:
        return Response.json({"error": str(exc)}, status=400)
    # Rotating the secret invalidated this session; force a fresh login.
    resp = Response.json({"ok": True})
    resp.headers["Set-Cookie"] = (
        f"{_SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    )
    return resp


# --------------------------------------------------------------------------
# Search (background discovery job)
# --------------------------------------------------------------------------
def _start_search(app: WebApp, req: Request) -> Response:
    data = req.json()
    query = str(data.get("query", ""))
    provider = str(data.get("provider", ""))
    country = str(data.get("country", ""))
    asn = str(data.get("asn", ""))

    def _work(job) -> dict[str, Any]:
        records = _discover_and_filter(query, provider, country, asn)
        job.progress = 1.0
        return {"count": len(records), "records": [_record_dict(r) for r in records]}

    job = app.jobs.start("search", _work, meta={"query": query})
    return Response.json({"job_id": job.id})


def _discover_and_filter(query: str, provider: str, country: str, asn: str) -> list:
    """Run discovery + seed providers, filtered by the web search terms.

    Reuses ``pipeline.discover``/``process`` and ``providers.load_seed_records``
    (the same sources the terminal menu uses) plus the shared ``record_matches``
    predicate, so the web search behaves identically to the CLI/menu.
    """
    from .. import pipeline
    from ..config import load_config
    from ..interactive import providers as providers_mod
    from ..interactive.filters_shared import record_matches
    from ..models import Filters

    records: list = list(providers_mod.load_seed_records())
    try:
        config = load_config()
        filters = Filters(
            countries=[country] if country else [],
            asns=[asn] if asn else [],
            providers=[provider] if provider else [],
        )
        raw = pipeline.discover(config, filters, verbose_errors=False)
        records.extend(pipeline.process(raw, filters))
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        log.warning("web search discovery failed: %s: %s", type(exc).__name__, exc)

    seen: set[str] = set()
    out: list = []
    for rec in records:
        prefix = getattr(rec, "prefix", None)
        if not prefix or prefix in seen:
            continue
        if record_matches(rec, query=query, provider=provider, country=country, asn=asn):
            seen.add(prefix)
            out.append(rec)
    return out


def _get_job(app: WebApp, req: Request) -> Response:
    job = app.jobs.get(req.query_one("id"))
    if job is None:
        return Response.json({"error": "no such job"}, status=404)
    return Response.json(job.to_dict())


# --------------------------------------------------------------------------
# Scan (bidirectional) — synchronous-per-request but hosts are bounded
# --------------------------------------------------------------------------
def _run_scan(app: WebApp, req: Request) -> Response:
    data = req.json()
    cidrs = data.get("cidrs")
    category = str(data.get("category", ""))

    def _work(job) -> dict[str, Any]:
        return _scan_and_store(app, cidrs, category)

    job = app.jobs.start("scan", _work, meta={"category": category})
    return Response.json({"job_id": job.id})


def _scan_and_store(app: WebApp, cidrs: Any, category: str) -> dict[str, Any]:
    from ..interactive import ranges as ranges_mod
    from ..interactive import scanner
    from ..interactive.settings import load_settings

    settings = load_settings()
    cidr_list: list[str] = []
    if isinstance(cidrs, list):
        cidr_list = [str(c) for c in cidrs]
    elif category in ranges_mod.CATEGORIES:
        cidr_list = [e.cidr for e in ranges_mod.category_entries(category)]

    hosts = ranges_mod.expand_hosts(
        cidr_list,
        sample_per_range=settings.sample_per_range,
        max_hosts=settings.max_hosts,
    )
    report = scanner.run_scan(category or "web", settings, hosts=hosts)
    scan_id = scanner.persist(report, app.store)
    rows = [_combined_row(report, c) for c in report.combined]
    return {
        "scan_id": scan_id,
        "counts": report.combined_counts,
        "results": rows,
    }


def _combined_row(report, c) -> dict[str, Any]:
    verdict = report.combined_verdict(c)
    p = c.probe
    return {
        "host": p.host,
        "health": next((v for pr, v in report.results if pr.host == p.host), ""),
        "avg_ms": p.avg_ms,
        "loss_pct": p.loss_pct,
        "iran_reachable": c.iran_reachable,
        "abroad_reachable": c.abroad_reachable,
        "abroad_nodes_ok": c.abroad_nodes_ok,
        "abroad_nodes_total": c.abroad_nodes_total,
        "abroad_status": getattr(c, "abroad_status", None),
        "combined": verdict,
        "open_ports": list(c.open_ports),
    }


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def _history(app: WebApp, req: Request) -> Response:
    scans = app.store.list_scans(limit=int(req.query_one("limit", "50") or 50))
    return Response.json(
        {
            "scans": [
                {
                    "id": s.id,
                    "started_at": s.started_at,
                    "scope": s.scope,
                    "total": s.total,
                    "good": s.good,
                    "medium": s.medium,
                    "bad": s.bad,
                }
                for s in scans
            ]
        }
    )


def _scan_results(app: WebApp, req: Request) -> Response:
    try:
        scan_id = int(req.query_one("id"))
    except ValueError:
        return Response.json({"error": "bad scan id"}, status=400)
    rows = app.store.get_results(scan_id)
    return Response.json(
        {
            "results": [
                {
                    "host": r.host,
                    "health": r.verdict,
                    "avg_ms": r.avg_ms,
                    "loss_pct": r.loss_pct,
                    "abroad_reachable": r.abroad_reachable,
                    "abroad_nodes_ok": r.abroad_nodes_ok,
                    "abroad_nodes_total": r.abroad_nodes_total,
                    "abroad_status": r.abroad_status,
                    "combined": r.combined_verdict,
                    "open_ports": r.open_ports,
                }
                for r in rows
            ]
        }
    )


# --------------------------------------------------------------------------
# Settings (shared file with CLI/menu)
# --------------------------------------------------------------------------
def _get_settings(app: WebApp, req: Request) -> Response:
    from ..interactive.settings import load_settings

    return Response.json({"settings": load_settings().to_dict()})


def _save_settings(app: WebApp, req: Request) -> Response:
    from ..interactive.settings import Settings, load_settings, save_settings

    data = req.json()
    current = load_settings().to_dict()
    known = set(current)
    for key, value in data.items():
        if key in known:
            current[key] = value
    try:
        settings = Settings(**current).clamped()
    except (TypeError, ValueError) as exc:
        return Response.json({"error": f"invalid settings: {exc}"}, status=400)
    save_settings(settings)
    return Response.json({"ok": True, "settings": settings.to_dict()})


# --------------------------------------------------------------------------
# Provider/country connectivity summary (Part C.5 widget)
# --------------------------------------------------------------------------
def _summary(app: WebApp, req: Request) -> Response:
    from .summary import provider_connectivity

    return Response.json({"providers": provider_connectivity(app.store)})


# --------------------------------------------------------------------------
# Export (CSV / JSON / whitelist bare IPs)
# --------------------------------------------------------------------------
def _export(app: WebApp, req: Request) -> Response:
    kind = req.query_one("kind", "json").lower()
    try:
        scan_id = int(req.query_one("scan"))
    except ValueError:
        return Response.json({"error": "bad scan id"}, status=400)
    rows = app.store.get_results(scan_id)

    if kind == "whitelist":
        from ..interactive.filters_shared import format_bare_ips

        hosts = [r.host for r in rows if r.combined_verdict == "INTERNATIONAL"]
        return Response.download(
            format_bare_ips(hosts),
            filename=f"whitelist-scan-{scan_id}.txt",
            content_type="text/plain; charset=utf-8",
        )

    records = [_row_to_record(r) for r in rows]
    if kind == "csv":
        from ..reporting import to_csv

        return Response.download(
            to_csv(records),
            filename=f"scan-{scan_id}.csv",
            content_type="text/csv; charset=utf-8",
        )
    from ..reporting import to_json

    return Response.download(
        to_json(records),
        filename=f"scan-{scan_id}.json",
        content_type="application/json; charset=utf-8",
    )


def _row_to_record(row):
    """Adapt a stored ResultRow into an IPRecord for the shared exporters."""
    from ..models import IPRecord

    rec = IPRecord(prefix=row.host, source="scan")
    rec.open_ports = list(row.open_ports)
    note_bits = [f"health={row.verdict}"]
    if row.combined_verdict:
        note_bits.append(f"combined={row.combined_verdict}")
    rec.notes = "; ".join(note_bits)
    return rec


def _record_dict(rec) -> dict[str, Any]:
    return {
        "prefix": getattr(rec, "prefix", ""),
        "asn": getattr(rec, "asn", None),
        "organization": getattr(rec, "organization", None),
        "country": getattr(rec, "country", None),
        "provider": getattr(rec, "provider", None),
    }
