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

from dataclasses import dataclass, field

from .settings import Settings

GOOD = "GOOD"
MEDIUM = "MEDIUM"
BAD = "BAD"

# Colour codes for terminal rendering (see progress/menu). Empty when colour
# is disabled by the caller.
_LABEL_ORDER = (GOOD, MEDIUM, BAD)

# Combined (bidirectional) reachability verdicts. A host is only fully
# "international"/whitelisted when it answers BOTH from Iran (local probe) and
# from abroad (check-host.net). "not checked" (abroad_reachable is None) is a
# distinct, displayable state — never conflated with an actual FAIL.
INTERNATIONAL = "INTERNATIONAL"
IRAN_ONLY = "IRAN_ONLY"
ABROAD_ONLY = "ABROAD_ONLY"
UNREACHABLE = "UNREACHABLE"

_COMBINED_ORDER = (INTERNATIONAL, IRAN_ONLY, ABROAD_ONLY, UNREACHABLE)


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


@dataclass(slots=True)
class CombinedResult:
    """A local probe enriched with abroad-reachability and open-port data.

    Wraps :class:`ProbeResult` rather than extending it so the many existing
    ``ProbeResult`` usages (pinger, storage, report, menu) keep working while
    the interactive scan path gains bidirectional + port visibility.
    ``abroad_reachable`` is a tri-state: ``True``/``False`` when the abroad
    check ran, ``None`` when it was skipped/disabled/not-applicable.
    """

    probe: ProbeResult
    abroad_reachable: bool | None = None
    abroad_nodes_ok: int = 0
    abroad_nodes_total: int = 0
    open_ports: list[int] = field(default_factory=list)
    # Distinguishes a real abroad answer from a provider outage vs. not-checked.
    # One of the ABROAD_* status strings (see global_check); defaults to the
    # "not checked / not applicable" state so pre-existing constructions and the
    # abroad-disabled path read back exactly as before.
    abroad_status: str = "not_applicable"

    @property
    def host(self) -> str:
        return self.probe.host

    @property
    def iran_reachable(self) -> bool:
        return self.probe.reachable


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


def classify_bidirectional(iran_reachable: bool, abroad_reachable: bool | None) -> str:
    """Combine local + abroad reachability into a single verdict.

    ``abroad_reachable is None`` means the abroad check did not run (disabled,
    skipped, or non-applicable) — treated as "unknown abroad", so the verdict
    reflects the local result alone rather than pretending abroad FAILed.
    """
    if abroad_reachable is None:
        return IRAN_ONLY if iran_reachable else UNREACHABLE
    if iran_reachable and abroad_reachable:
        return INTERNATIONAL
    if iran_reachable and not abroad_reachable:
        return IRAN_ONLY
    if abroad_reachable and not iran_reachable:
        return ABROAD_ONLY
    return UNREACHABLE


def summarize(labels: list[str]) -> dict[str, int]:
    """Count occurrences of each verdict, always returning all three keys."""
    counts = {label: 0 for label in _LABEL_ORDER}
    for label in labels:
        if label in counts:
            counts[label] += 1
    return counts


def summarize_combined(labels: list[str]) -> dict[str, int]:
    """Count combined verdicts, always returning all four keys."""
    counts = {label: 0 for label in _COMBINED_ORDER}
    for label in labels:
        if label in counts:
            counts[label] += 1
    return counts

