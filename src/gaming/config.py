"""Configuration loading and validation.

Configuration is layered:  built-in defaults  <  TOML file  <  CLI overrides.
Uses the stdlib ``tomllib`` (Python 3.11+), so no external dependency.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

from .models import Filters


DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "log_level": "INFO",
        "concurrency": 16,
        "timeout": 5.0,
    },
    "discovery": {
        # Sources that are enabled by default.
        "sources": ["rdap", "whois", "asn_bgp", "peeringdb", "rir"],
        # If offline, sources return their bundled sample data instead of failing.
        "offline": False,
    },
    "filters": {
        "countries": [],
        "asns": [],
        "providers": [],
        "organizations": [],
        "iran_datacenter": False,
        "foreign_datacenter": False,
    },
    "reachability": {
        "enabled": True,
        "method": "auto",  # auto | ping | tcp
        "tcp_probe_port": 80,
        "ports": [],  # optional additional port scan
    },
    "global_check": {
        "enabled": False,
        "provider": "check-host.net",
        "max_targets": 10,
    },
}


@dataclass(slots=True)
class Config:
    general: dict[str, Any] = field(default_factory=dict)
    discovery: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    reachability: dict[str, Any] = field(default_factory=dict)
    global_check: dict[str, Any] = field(default_factory=dict)

    # ---- convenience accessors -------------------------------------------
    @property
    def log_level(self) -> str:
        return str(self.general.get("log_level", "INFO"))

    @property
    def concurrency(self) -> int:
        return int(self.general.get("concurrency", 16))

    @property
    def timeout(self) -> float:
        return float(self.general.get("timeout", 5.0))

    @property
    def offline(self) -> bool:
        return bool(self.discovery.get("offline", False))

    @property
    def sources(self) -> list[str]:
        return list(self.discovery.get("sources", []))

    def to_filters(self) -> Filters:
        f = self.filters
        return Filters(
            countries=list(f.get("countries", [])),
            asns=[str(a) for a in f.get("asns", [])],
            providers=list(f.get("providers", [])),
            organizations=list(f.get("organizations", [])),
            iran_datacenter=bool(f.get("iran_datacenter", False)),
            foreign_datacenter=bool(f.get("foreign_datacenter", False)),
        )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Optional[str | Path] = None) -> Config:
    """Load configuration from an optional TOML file, merged over defaults."""
    merged = DEFAULT_CONFIG
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config file not found: {p}")
        try:
            with p.open("rb") as fh:
                file_data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid TOML in {p}: {exc}") from exc
        merged = _deep_merge(DEFAULT_CONFIG, file_data)

    return Config(
        general=merged.get("general", {}),
        discovery=merged.get("discovery", {}),
        filters=merged.get("filters", {}),
        reachability=merged.get("reachability", {}),
        global_check=merged.get("global_check", {}),
    )


def apply_overrides(config: Config, overrides: dict[str, Any]) -> Config:
    """Return a new Config with non-None override values applied.

    Overrides are namespaced dotted keys, e.g. ``{"general.concurrency": 32}``.
    """
    general = dict(config.general)
    discovery = dict(config.discovery)
    filters = dict(config.filters)
    reachability = dict(config.reachability)
    global_check = dict(config.global_check)

    buckets = {
        "general": general,
        "discovery": discovery,
        "filters": filters,
        "reachability": reachability,
        "global_check": global_check,
    }

    for dotted, value in overrides.items():
        if value is None:
            continue
        section, _, key = dotted.partition(".")
        if section in buckets and key:
            buckets[section][key] = value

    return replace(
        config,
        general=general,
        discovery=discovery,
        filters=filters,
        reachability=reachability,
        global_check=global_check,
    )
