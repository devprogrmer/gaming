"""Simplified, Check-Host-style health classification.

Raw ping diagnostics (latency samples, packet counts) are noisy and hard to
read at a glance. This module reduces a per-host probe result into a single,
user-facing verdict:

    GOOD    reachable, low latency, low packet loss
    MEDIUM  reachable, but higher latency or moderate loss
    BAD     unreachable, or high packet loss

Thresholds come from :class:`gaming.interactive.settings.Settings`, so they
are user-tunable from the Settings menu.
"""

from __future__ import annotations

from dataclasses import dataclass

from .settings import Settings

GOOD = "GOOD"
MEDIUM = "MEDIUM"
BAD = "BAD"

# Colour codes for terminal rendering (see progress/menu). Empty when colour
# is disabled by the caller.
_LABEL_ORDER = (GOOD, MEDIUM, BAD)


@dataclass(slots=True)
class ProbeResult:
    """Outcome of probing a single host."""

    host: str
    sent: int = 0
    received: int = 0
    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None

    @property
    def loss_pct(self) -> float:
        if self.sent <= 0:
            return 100.0
        return 100.0 * (self.sent - self.received) / self.sent

    @property
    def reachable(self) -> bool:
        return self.received > 0


def classify(result: ProbeResult, settings: Settings) -> str:
    """Return ``GOOD`` / ``MEDIUM`` / ``BAD`` for a probe result."""
    s = settings.clamped()

    # Unreachable is unambiguously BAD.
    if not result.reachable or result.avg_ms is None:
        return BAD

    loss = result.loss_pct
    avg = result.avg_ms

    # High loss dominates regardless of latency.
    if loss >= s.medium_loss_pct:
        return BAD

    if avg <= s.good_latency_ms and loss <= s.good_loss_pct:
        return GOOD

    if avg <= s.medium_latency_ms and loss < s.medium_loss_pct:
        return MEDIUM

    # Reachable but slow / lossy beyond the MEDIUM envelope.
    return BAD


def summarize(labels: list[str]) -> dict[str, int]:
    """Count occurrences of each verdict, always returning all three keys."""
    counts = {label: 0 for label in _LABEL_ORDER}
    for label in labels:
        if label in counts:
            counts[label] += 1
    return counts
