"""Tests for the three surfaces of on-demand provider lookup by name.

The lookup itself is covered by ``test_provider_lookup.py``; these tests exist
to prove that the CLI, the interactive menu, and the web endpoint all reach the
*same* function, so a name can never resolve differently depending on where it
was typed.
"""

from __future__ import annotations

import io
import json

import pytest

from gaming.discovery import provider_lookup
from gaming.discovery.provider_lookup import ProviderLookupResult
from gaming.models import IPRecord


def _result(name="Acme", prefixes=(("203.0.113.0/24", "Acme Corp"),), **kw):
    return ProviderLookupResult(
        name=name,
        records=[
            IPRecord(prefix=p, source="rdap-name", organization=o, provider=o)
            for p, o in prefixes
        ],
        sources_queried=kw.get("sources_queried", ["arin", "ripe"]),
        errors=kw.get("errors", []),
    )


@pytest.fixture
def spy(monkeypatch):
    """Record every call to the shared lookup and return a canned result.

    Patching the function *in its defining module* means a surface that
    reimplemented the lookup instead of calling the shared one would show up as
    an empty call list, which is exactly what these tests are checking.
    """
    calls: list[str] = []
    box: dict[str, ProviderLookupResult] = {"result": _result()}

    def _fake(name, *, timeout=15.0, limit=200):
        calls.append(name)
        box["result"].name = name
        return box["result"]

    monkeypatch.setattr(provider_lookup, "lookup_provider_by_name", _fake)
    return calls, box


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A WebApp isolated to tmp_path, matching test_web_server's fixture."""
    from gaming.interactive.storage import HistoryStore
    from gaming.web.auth import CredentialStore, RateLimiter
    from gaming.web.handlers import WebApp

    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    store = HistoryStore(tmp_path / "h.db")
    creds = CredentialStore(tmp_path / "creds.json")
    _c, password = creds.ensure_credentials()
    app = WebApp(
        credentials=creds, store=store, rate_limiter=RateLimiter(max_attempts=3)
    )
    return app, creds, password


# ---- CLI -----------------------------------------------------------------
def test_cli_provider_name_uses_the_shared_lookup(spy, capsys):
    from gaming.cli import main

    calls, _box = spy
    code = main(["discover", "--provider-name", "Acme Corp"])

    assert calls == ["Acme Corp"]
    assert code == 0
    assert "203.0.113.0/24" in capsys.readouterr().out


def test_cli_provider_name_does_not_run_seeded_discovery(spy, monkeypatch):
    """--provider-name replaces the seeded sources rather than filtering them.

    Running discovery first and then filtering by name is precisely the broken
    behaviour this flag exists to bypass.
    """
    import gaming.cli as cli_mod

    def _boom(*a, **kw):
        raise AssertionError("seeded discovery must not run for --provider-name")

    monkeypatch.setattr(cli_mod, "discover", _boom)
    assert cli_mod.main(["discover", "--provider-name", "Acme"]) == 0


def test_cli_exit_codes_separate_not_found_from_lookup_failure(spy, capsys):
    from gaming.cli import main

    _calls, box = spy

    box["result"] = _result(prefixes=())
    assert main(["discover", "--provider-name", "Nope"]) == 1

    box["result"] = _result(prefixes=(), sources_queried=[], errors=["arin: timed out"])
    assert main(["discover", "--provider-name", "Nope"]) == 2
    assert "Could not reach any registry" in capsys.readouterr().out


def test_cli_provider_name_honours_json_format(spy, capsys):
    from gaming.cli import main

    assert main(["discover", "--provider-name", "Acme", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["prefix"] == "203.0.113.0/24"


def test_existing_provider_flag_is_unchanged(spy):
    """--provider must keep its substring-filter meaning, not become a lookup."""
    from gaming.cli import build_parser

    args = build_parser().parse_args(["discover", "--provider", "arvan"])
    assert args.provider == "arvan"
    assert args.provider_name is None


# ---- menu ----------------------------------------------------------------
def _menu_context(inputs):
    from gaming.interactive.actions.context import ActionContext
    from gaming.interactive.settings import Settings
    from gaming.interactive.storage import HistoryStore

    out = io.StringIO()
    pending = list(inputs)

    def _prompt(_message):
        return pending.pop(0) if pending else ""

    return out, ActionContext(
        settings=Settings(),
        store=HistoryStore(":memory:"),
        stdout=out,
        print_=lambda text="": out.write(text + "\n"),
        prompt=_prompt,
        choose=lambda _t, _o: None,
    )


def test_menu_option_uses_the_shared_lookup(spy):
    from gaming.interactive.actions import lookup_provider

    calls, _box = spy
    out, ctx = _menu_context(["Acme Corp", "n"])
    lookup_provider(ctx)

    assert calls == ["Acme Corp"]
    assert "203.0.113.0/24" in out.getvalue()


def test_menu_option_reports_not_found_plainly(spy):
    from gaming.interactive.actions import lookup_provider

    _calls, box = spy
    box["result"] = _result(prefixes=())
    out, ctx = _menu_context(["Nope"])
    lookup_provider(ctx)

    text = out.getvalue()
    assert "No organization matching 'Nope'" in text
    assert "ARIN and RIPE" in text


def test_menu_option_is_listed_in_the_menu():
    from gaming.interactive.menu import _MENU_OPTIONS

    labels = {key: label for key, label in _MENU_OPTIONS}
    assert "10" in labels
    assert "provider" in labels["10"].lower()


# ---- web -----------------------------------------------------------------
def test_web_endpoint_uses_the_shared_lookup(env, spy):
    from tests.test_web_server import _login, _poll_job_done, _req

    app, creds, pw = env
    cookie = _login(app, creds, pw)
    calls, _box = spy

    start = app.handle(
        _req("POST", "/api/provider-lookup", body={"name": "Acme Corp"}, cookie=cookie)
    )
    job = _poll_job_done(app, cookie, json.loads(start.body)["job_id"])

    assert calls == ["Acme Corp"]
    assert job["status"] == "done"
    assert job["result"]["found"] is True
    assert job["result"]["records"][0]["prefix"] == "203.0.113.0/24"


def test_web_endpoint_distinguishes_not_found_from_unreachable(env, spy):
    from tests.test_web_server import _login, _poll_job_done, _req

    app, creds, pw = env
    cookie = _login(app, creds, pw)
    _calls, box = spy

    box["result"] = _result(prefixes=())
    start = app.handle(
        _req("POST", "/api/provider-lookup", body={"name": "Nope"}, cookie=cookie)
    )
    job = _poll_job_done(app, cookie, json.loads(start.body)["job_id"])
    assert job["result"]["found"] is False
    assert job["result"]["lookup_failed"] is False

    box["result"] = _result(prefixes=(), sources_queried=[], errors=["arin: timed out"])
    start = app.handle(
        _req("POST", "/api/provider-lookup", body={"name": "Nope"}, cookie=cookie)
    )
    job = _poll_job_done(app, cookie, json.loads(start.body)["job_id"])
    assert job["result"]["lookup_failed"] is True


def test_web_endpoint_rejects_an_empty_name(env, spy):
    from tests.test_web_server import _login, _req

    app, creds, pw = env
    cookie = _login(app, creds, pw)
    resp = app.handle(
        _req("POST", "/api/provider-lookup", body={"name": "  "}, cookie=cookie)
    )
    assert resp.status == 400
    calls, _box = spy
    assert calls == []


def test_web_endpoint_requires_authentication(env, spy):
    from tests.test_web_server import _req

    app, _creds, _pw = env
    resp = app.handle(_req("POST", "/api/provider-lookup", body={"name": "Acme"}))
    assert resp.status == 401


# ---- all three agree -----------------------------------------------------
def test_a_name_matching_several_organizations_is_reported_by_every_surface(env, spy):
    """The multi-organization case must not collapse on any surface."""
    from tests.test_web_server import _login, _poll_job_done, _req

    from gaming.cli import main
    from gaming.interactive.actions import lookup_provider

    _calls, box = spy
    box["result"] = _result(
        name="Apex",
        prefixes=(
            ("203.0.113.0/24", "Apex Networks LLC"),
            ("198.51.100.0/24", "Apex Hosting Canada"),
            ("192.0.2.0/24", "Apex Systems GmbH"),
        ),
    )
    expected = {"Apex Networks LLC", "Apex Hosting Canada", "Apex Systems GmbH"}

    app, creds, pw = env
    cookie = _login(app, creds, pw)
    start = app.handle(
        _req("POST", "/api/provider-lookup", body={"name": "Apex"}, cookie=cookie)
    )
    job = _poll_job_done(app, cookie, json.loads(start.body)["job_id"])
    assert set(job["result"]["organizations"]) == expected

    out, ctx = _menu_context(["Apex", "n"])
    lookup_provider(ctx)
    menu_text = out.getvalue()
    assert all(org in menu_text for org in expected)

    assert main(["discover", "--provider-name", "Apex", "--format", "json"]) == 0
