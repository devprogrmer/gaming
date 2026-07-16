"""Global reachability checks via check-host.net (or a compatible service).

check-host.net exposes a simple JSON API: a request kicks off distributed
probes from many nodes and returns a request id; a second endpoint returns
per-node results. This module is best-effort and fully degrades (returns
``None``) when the service is unavailable or disabled.

Only public IP addresses are submitted; private/reserved addresses are
skipped for privacy and correctness.
"""

from __future__ import annotations

import ipaddress
import time
from typing import Optional

from ..logging_setup import get_logger
from ..models import IPRecord
from ..utils.http import HTTPError, get_json

log = get_logger("gaming.reachability.global")

_BASE = "https://check-host.net"


def _is_public(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def global_reachability(
    host: str,
    *,
    timeout: float = 5.0,
    check_type: str = "tcp",
    port: int = 80,
    poll_attempts: int = 3,
    poll_interval: float = 2.0,
) -> Optional[bool]:
    """Query check-host.net for global reachability of ``host``.

    Returns True if any node reports success, False if nodes ran but none
    succeeded, and None if the check could not be performed.
    """
    if not _is_public(host):
        log.debug("skipping global check for non-public host %s", host)
        return None

    target = f"{host}:{port}" if check_type == "tcp" else host
    start_url = f"{_BASE}/check-{check_type}?host={target}&max_nodes=8"
    try:
        started = get_json(
            start_url, timeout=timeout, headers={"Accept": "application/json"}
        )
    except HTTPError as exc:
        log.debug("global check start failed for %s: %s", host, exc)
        return None

    request_id = started.get("request_id")
    if not request_id:
        return None

    result_url = f"{_BASE}/check-result/{request_id}"
    for _ in range(max(1, poll_attempts)):
        time.sleep(poll_interval)
        try:
            results = get_json(result_url, timeout=timeout)
        except HTTPError as exc:
            log.debug("global check poll failed for %s: %s", host, exc)
            continue
        verdict = _interpret(results)
        if verdict is not None:
            return verdict
    return None


def _interpret(results: dict) -> Optional[bool]:
    """Interpret check-host.net per-node results.

    Node value shapes vary; we treat any node whose result indicates a
    successful connection/time as reachable.
    """
    if not isinstance(results, dict):
        return None
    seen_any = False
    for node, value in results.items():
        if value is None:
            # Still pending for this node.
            continue
        seen_any = True
        # For tcp: value like [{"time": 0.12, "address": "1.2.3.4"}]
        # For ping: value like [[["OK", 0.1], ...]]
        if _node_ok(value):
            return True
    return False if seen_any else None


def _node_ok(value) -> bool:
    try:
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return "error" not in first and (
                    "time" in first or "address" in first
                )
            if isinstance(first, list) and first:
                inner = first[0]
                if isinstance(inner, list) and inner:
                    return str(inner[0]).upper() == "OK"
    except (IndexError, TypeError, ValueError):
        return False
    return False
