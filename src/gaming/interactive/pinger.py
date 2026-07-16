"""Cross-platform latency + packet-loss measurement.

The measurement engine sends several ICMP echo requests per host using the
operating system's own ``ping`` binary (no ``fping``, no raw sockets, no root),
parses the round-trip time out of each reply, and aggregates the samples into a
:class:`~gaming.interactive.classify.ProbeResult` (sent / received / avg / min /
max). When ICMP is blocked, it falls back to timed TCP connects so a host that
answers on a common port is still measured rather than reported as dead.

Everything is standard-library only and safe to run concurrently.
"""

from __future__ import annotations

import platform
import re
import socket
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..logging_setup import get_logger
from .classify import ProbeResult

log = get_logger("gaming.interactive.pinger")

_IS_WINDOWS = platform.system().lower() == "windows"

# Matches "time=1.23 ms", "time=1ms", "time<1ms", "time=1.23ms" across the
# common English ping outputs on Windows, Linux, and macOS.
_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)

# Positive evidence of a real reply on Windows (where the exit code is
# unreliable): a "Reply from ... bytes=" or "TTL=" line.
_WINDOWS_REPLY_RE = re.compile(r"bytes=|ttl=", re.IGNORECASE)

# Default ports tried, in order, for the TCP fallback when ICMP yields nothing.
_TCP_FALLBACK_PORTS = (443, 80)


def _ping_once(host: str, timeout: float) -> float | None:
    """Send a single ICMP echo. Return RTT in ms, or None if no reply."""
    if _IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(max(1, int(timeout * 1000))), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.debug("ping error for %s: %s", host, exc)
        return None

    if proc.returncode != 0:
        return None

    output = proc.stdout or ""

    match = _TIME_RE.search(output)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # Windows `ping` exits 0 even when it prints "Request timed out" or
    # "Destination host unreachable" without a real reply. Require positive
    # evidence of a reply before trusting the wall-clock approximation.
    if _IS_WINDOWS and not _WINDOWS_REPLY_RE.search(output):
        return None

    # Reply received but RTT unparseable (e.g. localized output): approximate
    # with the wall-clock time of the subprocess as a coarse upper bound.
    return max(0.0, (time.monotonic() - start) * 1000.0)


def _tcp_once(host: str, port: int, timeout: float) -> float | None:
    """Time a single TCP connect. Return connect latency in ms, or None."""
    try:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
    except ValueError:
        family = socket.AF_INET
    start = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            if sock.connect_ex((host, port)) == 0:
                return (time.monotonic() - start) * 1000.0
    except OSError as exc:
        log.debug("tcp probe error %s:%d: %s", host, port, exc)
    return None


def ping_host(host: str, *, count: int = 4, timeout: float = 2.0) -> ProbeResult:
    """Probe a single host ``count`` times and aggregate the samples."""
    count = max(1, int(count))
    samples: list[float] = []

    for _ in range(count):
        rtt = _ping_once(host, timeout)
        if rtt is not None:
            samples.append(rtt)

    # ICMP produced nothing — try a TCP connect so firewalled-ICMP hosts that
    # still serve traffic are not misreported as dead.
    if not samples:
        for port in _TCP_FALLBACK_PORTS:
            rtt = _tcp_once(host, port, timeout)
            if rtt is not None:
                samples.append(rtt)
                break

    if not samples:
        return ProbeResult(host=host, sent=count, received=0)

    return ProbeResult(
        host=host,
        sent=count,
        received=len(samples),
        avg_ms=sum(samples) / len(samples),
        min_ms=min(samples),
        max_ms=max(samples),
    )


def scan_hosts(
    hosts: Iterable[str],
    *,
    count: int = 4,
    timeout: float = 2.0,
    concurrency: int = 32,
    on_result: Callable[[ProbeResult], None] | None = None,
) -> list[ProbeResult]:
    """Probe many hosts concurrently.

    ``on_result`` (if given) is invoked once per completed host with its
    :class:`ProbeResult`, enabling live progress reporting. Results are
    returned in input order.
    """
    host_list = list(hosts)
    if not host_list:
        return []

    results: dict[str, ProbeResult] = {}
    workers = max(1, min(int(concurrency), len(host_list)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(ping_host, h, count=count, timeout=timeout): h
            for h in host_list
        }
        for fut in as_completed(futures):
            host = futures[fut]
            try:
                probe = fut.result()
            except Exception as exc:  # noqa: BLE001 - never abort the whole scan
                log.debug("probe crashed for %s: %s", host, exc)
                probe = ProbeResult(host=host, sent=count, received=0)
            results[host] = probe
            if on_result is not None:
                on_result(probe)

    # Preserve input ordering for a stable, predictable report.
    return [results[h] for h in host_list if h in results]
