from __future__ import annotations

from pathlib import Path

import pytest

from gaming.config import DEFAULT_CONFIG, apply_overrides, load_config


def test_load_defaults():
    cfg = load_config(None)
    assert cfg.log_level == "INFO"
    assert cfg.concurrency == 16
    assert cfg.timeout == 5.0
    assert "rdap" in cfg.sources
    assert cfg.offline is False


def test_load_from_file(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text(
        """
[general]
concurrency = 4

[filters]
countries = ["IR"]
iran_datacenter = true

[reachability]
ports = [22, 443]
""".strip()
    )
    cfg = load_config(p)
    assert cfg.concurrency == 4
    # unspecified defaults preserved
    assert cfg.timeout == 5.0
    filters = cfg.to_filters()
    assert filters.countries == ["IR"]
    assert filters.iran_datacenter is True
    assert cfg.reachability["ports"] == [22, 443]


def test_load_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/definitely-not-here.toml")


def test_invalid_toml(tmp_path: Path):
    p = tmp_path / "bad.toml"
    p.write_text("this = = = broken")
    with pytest.raises(ValueError):
        load_config(p)


def test_apply_overrides_ignores_none():
    cfg = load_config(None)
    new = apply_overrides(cfg, {"general.concurrency": 32, "general.timeout": None})
    assert new.concurrency == 32
    assert new.timeout == 5.0
    # original unchanged (immutability of override semantics)
    assert cfg.concurrency == 16


def test_default_config_shape():
    assert set(DEFAULT_CONFIG) == {
        "general",
        "discovery",
        "filters",
        "reachability",
        "global_check",
    }
