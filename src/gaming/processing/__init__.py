"""Processing: normalization and filtering."""

from __future__ import annotations

from .filters import apply_filters, matches
from .normalize import collapse_prefixes, normalize_records

__all__ = [
    "normalize_records",
    "collapse_prefixes",
    "apply_filters",
    "matches",
]
