"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the src/ layout importable without installation.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gaming.models import IPRecord  # noqa: E402


@pytest.fixture
def sample_records() -> list[IPRecord]:
    return [
        IPRecord(
            prefix="185.143.232.0/22",
            source="rdap",
            asn="AS201133",
            organization="Pars Pardazesh",
            country="IR",
            provider="pars pardazesh",
            notes="iran dc",
        ),
        IPRecord(
            prefix="104.16.0.0/13",
            source="rdap",
            asn="AS13335",
            organization="Cloudflare, Inc.",
            country="US",
            provider="cloudflare",
            notes="foreign dc",
        ),
        IPRecord(
            prefix="2a01:4f8::/29",
            source="asn_bgp",
            asn="AS24940",
            organization="Hetzner Online",
            country="DE",
            provider="hetzner",
            notes="foreign dc v6",
        ),
    ]
