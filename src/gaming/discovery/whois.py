"""WHOIS-based discovery via the stdlib ``socket`` (port 43).

When online it can query a WHOIS server for a seed object; offline or on
failure it returns representative sample records.
"""

from __future__ import annotations

import socket

from ..models import IPRecord
from .base import Source


class WhoisSource(Source):
    name = "whois"

    _WHOIS_HOST = "whois.ripe.net"
    _WHOIS_PORT = 43

    def _discover_online(self) -> list[IPRecord]:
        # WHOIS free-text responses are not reliably machine-parseable across
        # registries. We only attempt a live query when a single ASN seed is
        # provided, and we parse conservatively; otherwise fall back.
        seeds = self.context.filters.asns
        if not seeds:
            return []
        try:
            self._raw_query(seeds[0])
        except OSError:
            return []
        # Parsing WHOIS route objects reliably is out of scope for the sample
        # implementation; return empty to trigger sample fallback.
        return []

    def _raw_query(self, obj: str) -> str:
        with socket.create_connection(
            (self._WHOIS_HOST, self._WHOIS_PORT), timeout=self.context.timeout
        ) as sock:
            sock.sendall(f"{obj}\r\n".encode())
            chunks: list[bytes] = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _sample_data(self) -> list[IPRecord]:
        return [
            IPRecord(
                prefix="91.99.0.0/16",
                source=self.name,
                asn="AS44244",
                organization="Irancell (sample)",
                country="IR",
                provider="irancell",
                notes="WHOIS sample — Iranian mobile/datacenter",
            ),
            IPRecord(
                prefix="45.90.58.0/24",
                source=self.name,
                asn="AS200019",
                organization="Alexhost SRL (sample)",
                country="MD",
                provider="alexhost",
                notes="WHOIS sample — foreign datacenter",
            ),
        ]
