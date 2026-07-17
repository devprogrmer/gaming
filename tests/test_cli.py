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


def test_update_subcommand_invokes_updater(capsys, monkeypatch):
    from gaming import updater
    from gaming.updater import UpdateResult

    captured = {}

    def _fake_run_update(*, source=None, pull=None, log=None, ref=None):
        captured["source"] = source
        captured["pull"] = pull
        captured["ref"] = ref
        return UpdateResult(
            previous_version="0.2.0",
            new_version="0.3.0",
            source="/srv/gaming",
            git_updated=True,
        )

    monkeypatch.setattr(updater, "run_update", _fake_run_update)
    rc = main(["update", "--source", "/srv/gaming"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["source"] == "/srv/gaming"
    assert "Updated gaming 0.2.0 -> 0.3.0" in out


def test_update_subcommand_no_pull_flag(capsys, monkeypatch):
    from gaming import updater
    from gaming.updater import UpdateResult

    captured = {}

    def _fake_run_update(*, source=None, pull=None, log=None, ref=None):
        captured["pull"] = pull
        return UpdateResult("0.3.0", "0.3.0", "/srv/gaming", git_updated=False)

    monkeypatch.setattr(updater, "run_update", _fake_run_update)
    rc = main(["update", "--no-pull"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["pull"] is False
    assert "already up to date" in out


def test_update_subcommand_reports_failure(capsys, monkeypatch):
    from gaming import updater
    from gaming.updater import UpdateError

    def _boom(*, source=None, pull=None, log=None, ref=None):
        raise UpdateError("source directory does not exist")

    monkeypatch.setattr(updater, "run_update", _boom)
    rc = main(["update"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "update failed" in err


def test_update_subcommand_switches_version(capsys, monkeypatch):
    from gaming import updater
    from gaming.updater import UpdateResult

    captured = {}

    def _fake_run_update(*, source=None, pull=None, log=None, ref=None):
        captured["ref"] = ref
        return UpdateResult(
            previous_version="0.3.0",
            new_version="0.1.0",
            source="/srv/gaming",
            git_updated=True,
            ref=ref,
        )

    monkeypatch.setattr(updater, "run_update", _fake_run_update)
    rc = main(["update", "--version", "v0.1.0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["ref"] == "v0.1.0"
    assert "Switched gaming 0.3.0 -> 0.1.0 (release v0.1.0)." in out


def test_update_subcommand_lists_releases(capsys, monkeypatch):
    from gaming import updater

    def _fake_list(source=None):
        return ["v0.3.0", "v0.2.0", "v0.1.0"]

    monkeypatch.setattr(updater, "list_releases", _fake_list)
    monkeypatch.setattr("gaming.cli.__version__", "0.2.0")
    rc = main(["update", "--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Available releases (newest first):" in out
    # The currently-installed version is marked with an asterisk.
    assert "  * v0.2.0" in out
    assert "    v0.3.0" in out
