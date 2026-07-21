"""Verdict-change alerting for scheduled scans (Part C).

When a recurring scheduled scan (see :mod:`.scheduler`) re-probes the same
scope, a host can flip between the whitelist state ``INTERNATIONAL`` and a
degraded one (``IRAN_ONLY`` / ``ABROAD_ONLY`` / ``UNREACHABLE``) — exactly the
event an operator watching international connectivity from Iran cares about.

This module diffs the two most recent scans for a scope and reports those
flips. It is opt-in (``Settings.alert_on_change``) and, when a webhook URL is
configured, POSTs a JSON payload via the stdlib :mod:`urllib.request`. Both the
diff and the webhook call are fully fail-soft: an error here never affects the
scan that produced the data.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from ..logging_setup import get_logger
from .settings import Settings
from .storage import HistoryStore

log = get_logger("gaming.interactive.alerts")

_WHITELIST = "INTERNATIONAL"


@dataclass(slots=True)
class VerdictChange:
    """One host's combined-verdict transition between two scans."""

    host: str
    previous: str
    current: str

    @property
    def gained_whitelist(self) -> bool:
        return self.current == _WHITELIST and self.previous != _WHITELIST

    @property
    def lost_whitelist(self) -> bool:
        return self.previous == _WHITELIST and self.current != _WHITELIST


def _combined_of(row) -> str:
    """The row's combined verdict, tolerating pre-migration rows."""
    if getattr(row, "combined_verdict", None):
        return row.combined_verdict
    # Pre-migration row (no abroad data): read local reachability only.
    return "IRAN_ONLY" if row.received > 0 else "UNREACHABLE"


def diff_last_two(store: HistoryStore, scope: str) -> list[VerdictChange]:
    """Return per-host verdict changes between the two latest scans of a scope.

    Only hosts present in *both* scans are compared (a host that appears or
    disappears between runs is not a "change" in reachability). Returns an empty
    list when there are fewer than two scans for the scope, or on any error.
    """
    try:
        scans = [s for s in store.list_scans(limit=200) if s.scope == scope]
        if len(scans) < 2:
            return []
        current, previous = scans[0], scans[1]  # list_scans is newest-first
        cur = {r.host: _combined_of(r) for r in store.get_results(current.id)}
        prev = {r.host: _combined_of(r) for r in store.get_results(previous.id)}
    except Exception as exc:  # noqa: BLE001 - alerting must never break a scan
        log.warning(
            "verdict diff failed for scope %s: %s: %s",
            scope,
            type(exc).__name__,
            exc,
        )
        return []

    changes: list[VerdictChange] = []
    for host, cur_verdict in cur.items():
        prev_verdict = prev.get(host)
        if prev_verdict is not None and prev_verdict != cur_verdict:
            changes.append(
                VerdictChange(host=host, previous=prev_verdict, current=cur_verdict)
            )
    return changes


def _post_webhook(url: str, payload: dict, *, timeout: float = 10.0) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - user-configured URL, opt-in
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = getattr(resp, "status", "?")
            log.info("verdict-change webhook posted (HTTP %s)", status)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("verdict-change webhook failed: %s: %s", type(exc).__name__, exc)


def process_scan_alerts(
    store: HistoryStore,
    scope: str,
    settings: Settings,
    *,
    scan_id: int | None = None,
) -> list[VerdictChange]:
    """Detect + report verdict changes after a scheduled scan of ``scope``.

    No-op (returns ``[]``) unless ``settings.alert_on_change`` is set. Each
    detected flip is logged; if ``settings.alert_webhook_url`` is non-empty a
    single JSON payload describing all changes is POSTed to it. Fail-soft
    throughout — the caller's scan result is never affected.
    """
    if not settings.alert_on_change:
        return []
    changes = diff_last_two(store, scope)
    if not changes:
        return []

    for ch in changes:
        direction = (
            "GAINED whitelist"
            if ch.gained_whitelist
            else "LOST whitelist"
            if ch.lost_whitelist
            else "changed"
        )
        log.warning(
            "verdict change [%s] %s: %s -> %s (%s)",
            scope,
            ch.host,
            ch.previous,
            ch.current,
            direction,
        )

    url = (settings.alert_webhook_url or "").strip()
    if url:
        payload = {
            "event": "verdict_change",
            "scope": scope,
            "scan_id": scan_id,
            "changes": [asdict(ch) for ch in changes],
        }
        _post_webhook(url, payload)
    return changes
