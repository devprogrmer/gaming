"""TCP port probing."""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from ..logging_setup import get_logger
from ..models import IPRecord

log = get_logger("gaming.reachability.ports")


def probe_port(host: str, port: int, *, timeout: float = 3.0) -> bool:
    """Return True if a TCP connection to ``host:port`` succeeds."""
    try:
        version = ipaddress.ip_address(host).version
        family = socket.AF_INET6 if version == 6 else socket.AF_INET
    except ValueError:
        family = socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError as exc:
        log.debug("port probe error %s:%d: %s", host, port, exc)
        return False


def probe_ports(
    host: str,
    ports: Iterable[int],
    *,
    timeout: float = 3.0,
    concurrency: int = 16,
) -> list[int]:
    """Return the sorted list of open ports for ``host``."""
    ports = list(dict.fromkeys(int(p) for p in ports))
    if not ports:
        return []
    open_ports: list[int] = []
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(ports)))) as pool:
        future_map = {
            pool.submit(probe_port, host, p, timeout=timeout): p for p in ports
        }
        for fut in as_completed(future_map):
            if fut.result():
                open_ports.append(future_map[fut])
    return sorted(open_ports)


def probe_records(
    records: Iterable[IPRecord],
    ports: Iterable[int],
    *,
    timeout: float = 3.0,
    concurrency: int = 16,
) -> list[IPRecord]:
    """Populate ``open_ports`` on each record."""
    recs = list(records)
    port_list = list(ports)
    if not port_list:
        return recs
    for rec in recs:
        rec.open_ports = probe_ports(
            rec.sample_host(),
            port_list,
            timeout=timeout,
            concurrency=concurrency,
        )
    return recs
