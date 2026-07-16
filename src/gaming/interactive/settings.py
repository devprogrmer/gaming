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
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
