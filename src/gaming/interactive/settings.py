"""Interactive-mode settings: thresholds and scan parameters.

These live in a standalone JSON file (``settings.json`` under the app home)
rather than in :data:`gaming.config.DEFAULT_CONFIG`, keeping the interactive
tunables independent of the core CLI configuration schema.

All values are user-facing and adjustable from the Settings menu.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any

from . import paths


@dataclass(slots=True)
class Settings:
    """User-adjustable parameters for interactive scans and classification."""

    # Classification thresholds (Check-Host style, latency in milliseconds).
    good_latency_ms: float = 80.0
    good_loss_pct: float = 10.0
    medium_latency_ms: float = 200.0
    medium_loss_pct: float = 40.0

    # Scan behaviour.
    ping_count: int = 4  # probes per host for latency/loss measurement
    concurrency: int = 32  # max hosts probed at once
    timeout: float = 2.0  # per-probe timeout in seconds
    sample_per_range: int = 16  # hosts sampled from each CIDR (0 = all, capped)
    max_hosts: int = 512  # overall safety cap per scan

    # Abroad (bidirectional) reachability via check-host.net. Default ON for
    # interactive scans (the whole point is "reachable from Iran AND abroad").
    check_global: bool = True  # run the abroad check alongside the local probe
    max_global_targets: int = 25  # cap abroad checks per scan (alive-first)
    global_min_ok_fraction: float = 0.5  # fraction of nodes that must succeed
    export_international_only: bool = False  # bare-IP export keeps only whitelist

    # Abroad-check provider selection (Part D). "check-host" (default),
    # "ripe-atlas", or "both". RIPE Atlas needs an API key (env var
    # GAMING_RIPE_ATLAS_KEY); with no key it is skipped and the tool falls back
    # to check-host.net, so this default is safe for users who set nothing up.
    abroad_provider: str = "check-host"

    # Optional common-ports TCP-connect scan (Part C). Off by default so the
    # ordinary latency scan stays fast; when on, each probed host also gets a
    # plain TCP connect against ``scan_ports`` and open ports show in results.
    scan_ports: bool = False  # probe common TCP ports alongside the latency scan
    ports: str = "80,443,22,21,25,53,3306,5432,6379,8080,8443"  # comma-sep preset

    # Verdict-change alerting for scheduled scans (Part C). Off by default. When
    # ``alert_on_change`` is on, a host flipping between INTERNATIONAL and
    # IRAN_ONLY/ABROAD_ONLY/UNREACHABLE across consecutive scheduled scans is
    # logged; if ``alert_webhook_url`` is also set, a JSON payload is POSTed to
    # it (stdlib ``urllib``). Empty URL means "log only, never call out".
    alert_on_change: bool = False  # detect + log verdict flips on scheduled scans
    alert_webhook_url: str = ""  # optional webhook for verdict-change alerts

    def clamped(self) -> Settings:
        """Return a copy with all values coerced into sane ranges."""
        return Settings(
            good_latency_ms=max(1.0, float(self.good_latency_ms)),
            good_loss_pct=min(100.0, max(0.0, float(self.good_loss_pct))),
            medium_latency_ms=max(
                float(self.good_latency_ms) + 1.0, float(self.medium_latency_ms)
            ),
            medium_loss_pct=min(100.0, max(0.0, float(self.medium_loss_pct))),
            ping_count=max(1, int(self.ping_count)),
            concurrency=max(1, int(self.concurrency)),
            timeout=max(0.1, float(self.timeout)),
            sample_per_range=max(0, int(self.sample_per_range)),
            max_hosts=max(1, int(self.max_hosts)),
            check_global=bool(self.check_global),
            max_global_targets=max(0, int(self.max_global_targets)),
            global_min_ok_fraction=min(1.0, max(0.0, float(self.global_min_ok_fraction))),
            export_international_only=bool(self.export_international_only),
            abroad_provider=_normalize_provider(self.abroad_provider),
            scan_ports=bool(self.scan_ports),
            ports=_normalize_ports(self.ports),
            alert_on_change=bool(self.alert_on_change),
            alert_webhook_url=str(self.alert_webhook_url or "").strip(),
        )

    def port_list(self) -> list[int]:
        """The configured common ports as a de-duplicated list of ints."""
        return _parse_ports(self.ports)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ports(raw: object) -> list[int]:
    """Parse a comma-separated port string into a de-duplicated 1-65535 list."""
    out: list[int] = []
    seen: set[int] = set()
    for tok in str(raw or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok.isdigit():
            continue
        port = int(tok)
        if 1 <= port <= 65535 and port not in seen:
            seen.add(port)
            out.append(port)
    return out


def _normalize_ports(raw: object) -> str:
    """Canonicalise a ports string (drop junk/dupes/out-of-range)."""
    return ",".join(str(p) for p in _parse_ports(raw))


_PROVIDER_CHOICES = ("check-host", "ripe-atlas", "both")


def _normalize_provider(raw: object) -> str:
    """Coerce the abroad-provider choice; unknown values fall back to check-host."""
    value = str(raw or "").strip().lower()
    return value if value in _PROVIDER_CHOICES else "check-host"



def _known_fields() -> set[str]:
    return {f.name for f in fields(Settings)}


def load_settings() -> Settings:
    """Load settings from disk, falling back to defaults for any missing keys."""
    path = paths.settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    known = _known_fields()
    filtered = {k: v for k, v in raw.items() if k in known}
    try:
        return Settings(**filtered).clamped()
    except (TypeError, ValueError):
        return Settings()


def save_settings(settings: Settings) -> None:
    """Persist settings to disk (pretty-printed JSON)."""
    path = paths.settings_path()
    path.write_text(
        json.dumps(settings.clamped().to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
