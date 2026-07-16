"""WHOIS-based discovery via the stdlib ``socket`` (port 43).

Given seed ASNs, this source issues an inverse WHOIS query
(``-i origin ASxxxx``) to a registry WHOIS server and parses the returned RPSL
``route:`` / ``route6:`` objects into records. Offline (``--offline``) or on any
failure it returns representative sample records.
"""

from __future__ import annotations

import socket

from ..models import IPRecord, normalize_asn
from .base import Source


class WhoisSource(Source):
    name = "whois"

    _WHOIS_HOST = "whois.radb.net"
    _WHOIS_PORT = 43

    def _discover_online(self) -> list[IPRecord]:
        seeds = self.context.filters.asns
        if not seeds:
            return []

        records: list[IPRecord] = []
        for seed in seeds:
            asn = normalize_asn(seed)
            try:
                response = self._raw_query(f"-i origin {asn}")
            except OSError as exc:
                self.log.debug("WHOIS query failed for %s: %s", asn, exc)
                continue
            records.extend(self._parse_routes(response, asn))
        return records

    def _raw_query(self, query: str) -> str:
        with socket.create_connection(
            (self._WHOIS_HOST, self._WHOIS_PORT), timeout=self.context.timeout
        ) as sock:
            sock.sendall(f"{query}\r\n".encode())
            chunks: list[bytes] = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _parse_routes(self, response: str, asn: str) -> list[IPRecord]:
        """Parse RPSL ``route``/``route6`` objects from a WHOIS response.

        RPSL objects are separated by blank lines; ``route:``/``route6:`` gives
        the prefix and ``descr:``/``origin:`` provide supporting metadata.
        """
        records: list[IPRecord] = []
        for block in response.split("\n\n"):
            prefix: str | None = None
            descr: str | None = None
            for raw in block.splitlines():
                key, sep, value = raw.partition(":")
                if not sep:
                    continue
                key = key.strip().lower()
                value = value.strip()
                if key in ("route", "route6") and value:
                    prefix = value
                elif key == "descr" and value and descr is None:
                    descr = value
            if not prefix:
                continue
            try:
                records.append(
                    IPRecord(
                        prefix=prefix,
                        source=self.name,
                        asn=asn,
                        organization=descr,
                        notes="WHOIS route object",
                    )
                )
            except ValueError:
                # Not a parseable prefix; skip it.
                continue
        return records

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
