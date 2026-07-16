"""Local reachability checks (alive / up status).

Two strategies:
  * ``ping``: ICMP echo via the system ``ping`` binary (no root needed).
  * ``tcp``:  a TCP connect to a probe port (works where ICMP is blocked).
  * ``auto``: try ping, then fall back to a TCP connect.

All checks run with per-host timeouts and are safe to call concurrently.
"""

from __future__ import annotations

import ipaddress
import platform
import socket
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..logging_setup import get_logger
from ..models import IPRecord

log = get_logger("gaming.reachability.local")


def _ping(host: str, timeout: float) -> bool:
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        # -c 1 one packet, -W timeout in seconds (Linux) — clamp to >=1.
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.debug("ping failed for %s: %s", host, exc)
        return False


def _tcp_connect(host: str, port: int, timeout: float) -> bool:
    try:
        family = (
            socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
        )
    except ValueError:
        family = socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
        return True
    except OSError as exc:
        log.debug("tcp connect failed for %s:%d: %s", host, port, exc)
        return False


def check_alive(
    host: str,
    *,
    method: str = "auto",
    timeout: float = 3.0,
    tcp_probe_port: int = 80,
) -> bool:
    """Return True if ``host`` appears reachable."""
    method = (method or "auto").lower()
    if method == "ping":
        return _ping(host, timeout)
    if method == "tcp":
        return _tcp_connect(host, tcp_probe_port, timeout)
    # auto
    if _ping(host, timeout):
        return True
    return _tcp_connect(host, tcp_probe_port, timeout)


def check_alive_bulk(
    records: Iterable[IPRecord],
    *,
    method: str = "auto",
    timeout: float = 3.0,
    tcp_probe_port: int = 80,
    concurrency: int = 16,
) -> list[IPRecord]:
    """Populate ``alive`` on each record concurrently. Returns the same list."""
    recs = list(records)
    if not recs:
        return recs

    def _worker(rec: IPRecord) -> None:
        host = rec.sample_host()
        try:
            rec.alive = check_alive(
                host,
                method=method,
                timeout=timeout,
                tcp_probe_port=tcp_probe_port,
            )
        except Exception as exc:  # noqa: BLE001 - never let one host abort the run
            rec.alive = False
            rec.notes = f"{rec.notes}; alive-check error: {exc}".strip("; ")

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(_worker, r) for r in recs]
        for _ in as_completed(futures):
            pass
    return recs
