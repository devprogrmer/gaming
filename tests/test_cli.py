from __future__ import annotations

import json

from gaming.cli import main


def test_sources_command(capsys):
    rc = main(["sources"])
    out = capsys.readouterr().out
    assert rc == 0
    for name in ("rdap", "whois", "asn_bgp", "peeringdb", "rir"):
        assert name in out


def test_menu_subcommand_invokes_menu(monkeypatch):
    calls = []
    import gaming.interactive.menu as menu

    monkeypatch.setattr(menu, "run", lambda argv=None: calls.append("menu") or 0)
    rc = main(["menu"])
    assert rc == 0
    assert calls == ["menu"]


def test_no_subcommand_defaults_to_menu(monkeypatch):
    calls = []
    import gaming.interactive.menu as menu

    monkeypatch.setattr(menu, "run", lambda argv=None: calls.append("menu") or 0)
    rc = main([])
    assert rc == 0
    assert calls == ["menu"]


def test_discover_offline_json(capsys):
    rc = main(["--offline", "discover", "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) > 0
    assert "prefix" in data[0]


def test_discover_offline_iran_focus(capsys):
    rc = main(["--offline", "discover", "--iran-datacenter", "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data, "expected at least one Iranian record"
    assert all(rec["country"] == "IR" for rec in data)


def test_discover_country_filter_csv(capsys):
    rc = main(["--offline", "discover", "--country", "US", "--format", "csv"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.strip().splitlines()
    assert lines[0].startswith("source,")
    # every data row should be US
    assert any(",US," in line for line in lines[1:])


def test_check_command_offline_no_global(capsys, monkeypatch):
    # Force TCP alive to a deterministic value to avoid real network.
    from gaming.reachability import local

    monkeypatch.setattr(local, "check_alive", lambda host, **kw: False)
    rc = main(["check", "192.0.2.1", "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data[0]["prefix"] == "192.0.2.1/32"
    assert data[0]["alive"] is False


def test_run_pipeline_offline(capsys, monkeypatch):
    from gaming.reachability import local

    monkeypatch.setattr(local, "check_alive", lambda host, **kw: True)
    rc = main(
        [
            "--offline",
            "run",
            "--foreign-datacenter",
            "--no-reachability",
            "--format",
            "json",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data
    assert all(rec["country"] != "IR" for rec in data)


def test_invalid_config_returns_2(capsys):
    rc = main(["--config", "/no/such/file.toml", "sources"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "config error" in err
